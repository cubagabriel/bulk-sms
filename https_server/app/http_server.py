#!/usr/bin/python3.8
"""
Author  : Gabriel Cuba
Desc    : implements REST API for the client, creates instances of load balancers
"""
import csv
import signal
import sys
from functools import partial
from bottle import run, request, post, get, delete, HTTPResponse, static_file
import os
import json
from load_balancer import LoadBalancer
import logging
import configparser
import chardet


# define function to load ID
def load_id():
    try:
        with open('/app/config/id.txt') as f:
            line = f.readline()
    except IOError:
        logger.error('Could not read ID file, will reset ID to 1')
        return 1
    try:
        idx = int(line)
    except ValueError:
        logger.error('ID in file is not an integer, will reset ID to 1')
        return 1
    return idx


# set logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(name)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
logger.addHandler(handler)

# load configuration
logger.info('loading configuration')
config = configparser.ConfigParser()
config.read('/app/config/global_config.ini')
MAX_SMS_LENGTH = int(config['front_end']['MAX_SMS_LENGTH'])
COUNTRY_PREFIX = config['front_end']['COUNTRY_PREFIX']
users = config['front_end']['users'].split(',')
save_path = config['front_end']['save_path']
result_path = config['front_end']['result_path']
logger.info('configuration loaded: {}'.format(json.dumps(dict(config['front_end']))))
# load ID
batch_request_id = load_id()
# create load balancers
logger.info('creating load balancers')
load_balancers_names = config['front_end']['load_balancers'].split(',')
load_balancers = dict()
for name in load_balancers_names:
    tps_per_worker = config[name]['MAX_TPS']
    worker_config = config[name]
    load_balancers[name] = LoadBalancer(max_workers=worker_config['max_workers'],
                                        tps_per_worker=worker_config['MAX_TPS'],
                                        time_per_worker=worker_config['time_per_worker'],
                                        expired_factor=worker_config['expired_factor'],
                                        sender_socket_port=worker_config['ventilator_push_port'],
                                        stop_socket_port=worker_config['controller_pubsub_port'],
                                        receiver_socket_port=worker_config['sink_push_port'],
                                        result_path=result_path)


# handle signals
def handle_exit(load_balancers, signal, frame):
    logger.info('received signal: {}'.format(signal))
    for lb in load_balancers.values():
        lb.terminate()
    logger.info('terminating program')
    sys.exit(1)


signal.signal(signal.SIGTERM, partial(handle_exit, load_balancers, ))
signal.signal(signal.SIGINT, partial(handle_exit, load_balancers, ))


@get('/api/v2/sms_batches/<priority:path>/<batch_id:int>/<resource:path>')
def batch_query(batch_id, priority, resource):
    if priority not in load_balancers_names:
        body = json.dumps({'message': 'invalid priority'})
        return HTTPResponse(status=400, body=body)
    if resource not in ['status, report']:
        pass
    if batch_id not in load_balancers[priority].jobs:
        body = json.dumps({'message': 'batch ID no existe'})
        return HTTPResponse(status=400, body=body)
    job = load_balancers[priority].jobs.get(batch_id)
    status = job.status
    # case status
    if resource == 'status':
        progress = "{:.2f} %".format(job.get_progress()*100)
        body = json.dumps({'status': status, 'progress': progress})
        return HTTPResponse(status=200, body=body)
    # case report
    elif resource == 'report':
        if status in ['assigned', 'starting', 'inprogress']:
            body = json.dumps({'message': 'El batch aun está en ejecución'.format(batch_id)})
            return HTTPResponse(status=202, body=body)
        filename = os.path.basename(job.result_file_name)
        path = os.path.dirname(job.result_file_name)
        return static_file(filename, root=path, download=True)
    else:
        body = json.dumps({'message': 'invalid request'})
        return HTTPResponse(status=400, body=body)


@post('/api/v2/sms_batches')
def send_batch():
    user = request.forms.get('user')
    message = request.forms.get('message')
    sender = request.forms.get('sender')
    priority = request.forms.get('priority')
    # validar usuario
    if user not in users:
        body = json.dumps({'message': 'user unknown'})
        return HTTPResponse(status=400, body=body)
    if priority not in load_balancers_names:
        body = json.dumps({'message': 'invalid priority'})
        return HTTPResponse(status=400, body=body)
    # validar sender
    try:
        sender = str(sender)
    except NameError:
        body = json.dumps({'message': 'sender is not a string'})
        return HTTPResponse(status=400, body=body)
    for c in sender:
        if not (c.isalnum() or c == '_' or c == '-'):
            body = json.dumps({'message': 'el sender debe ser alfanumerico y puede contener guiones'})
            return HTTPResponse(status=400, body=body)
    if not 3 <= len(sender) <= 11:
        body = json.dumps({'message': 'sender debería tener entre 3 y 11 caracteres'})
        return HTTPResponse(status=400, body=body)
    # validar mensaje
    if not isinstance(message, str) or not len(message) <= MAX_SMS_LENGTH:
        body = json.dumps({'message': 'el mensaje tiene una longitud o formato incorrecto'})
        return HTTPResponse(status=400, body=body)
    # almacenar archivo batch
    file = request.files.get('batch_file')
    _, ext = os.path.splitext(file.filename)
    if ext not in ('.csv', '.txt'):
        body = json.dumps({'message': 'file extension is incorrect'})
        return HTTPResponse(status=400, body=body)

    import datetime as dt
    date = dt.datetime.today().strftime("%Y%m%d%H%M")
    global batch_request_id
    new_filename = '{}_{}_{}.txt'.format(user, batch_request_id, date)
    file_path = "{path}/{file}".format(path=save_path, file=new_filename)
    # guardar batch
    try:
        file.save(file_path)
    except IOError as e:
        logger.error(str(e))
        body = json.dumps({'message': 'could not save file'})
        return HTTPResponse(status=500, body=body)
    # validar sintaxis del archivo y obtener tipo de batch
    file_ok, text, batch_type = check_file(file_path)
    if not file_ok:
        body = json.dumps({'message': text})
        return HTTPResponse(status=400, body=body)
    # intentar ejecutar batch
    load_balancers[priority].add_new_job(batch_request_id, file_path, message, batch_type, sender)
    load_balancers[priority].assign(new_job_flag=True)
    body = json.dumps({'id': batch_request_id, 'message': 'processing'})
    batch_request_id += 1
    persist_id(batch_request_id)
    return HTTPResponse(status=202, body=body)


@delete('/api/v2/sms_batches/<priority:path>/<batch_id:int>')
def stop_batch(batch_id, priority):
    if priority not in load_balancers_names:
        body = json.dumps({'message': 'invalid priority'})
        return HTTPResponse(status=400, body=body)
    if batch_id in load_balancers[priority].jobs:
        load_balancers[priority].stop_job(batch_id)
        body = json.dumps({'message': 'current batch has been stopped'})
        return HTTPResponse(status=200, body=body)
    else:
        body = json.dumps({'message': 'batch ID no existe'})
        return HTTPResponse(status=400, body=body)


def check_file(filepath):
    try:
        file_encoding = get_file_encoding(filepath)
        if not file_encoding:
            return False, 'no se puede determinar la codificación del archivo', ''
        with open(filepath, 'r', encoding=file_encoding) as file:
            try:
                # mas de una columna?
                dialect = csv.Sniffer().sniff(file.read(1024), delimiters=";,|")
                batch_type = '2'
                batch_data = {}
                if get_column_count(file, dialect) > 2:
                    return False, 'archivo tiene más de dos columnas', batch_type
                file.seek(0)
                csv_reader = csv.reader(file, dialect=dialect)
                for line in csv_reader:
                    batch_data[line[0]] = line[1]
                flag_msisdn_check, msisdn_error = check_msisdn_list(list(batch_data.keys()))
                flag_msg_check, msg_error = check_msg_list(list(batch_data.values()))
                return flag_msisdn_check and flag_msg_check, msisdn_error + '\n' + msg_error, batch_type
            except csv.Error as e:
                # una sola columna
                batch_type = '1'
                file.seek(0)
                batch_data = file.read().splitlines()
                flag, error = check_msisdn_list(batch_data)
                return flag, error, batch_type
    except Exception as e:
        logger.error(str(e))
        return False, 'no se pudo abrir el archivo', ''


def persist_id(new_idx):
    with open('/app/config/id.txt', 'w+') as f:
        f.write('{}'.format(new_idx))


def get_column_count(file, dialect):
    file.seek(0)
    csv_reader = csv.reader(file, dialect=dialect)
    return len(next(csv_reader))


def check_msisdn_list(list_of_msisdn):
    if len(list_of_msisdn) == 0:
        return False, 'el archivo cargado está vacío'
    for msisdn in list_of_msisdn:
        if (11 > len(msisdn) < 10) or not str(msisdn).startswith(COUNTRY_PREFIX):
            return False, 'una de las lineas del archivo contiene un numero con longitud o formato inválido'
    return True, ''


def check_msg_list(list_of_msg):
    for msg in list_of_msg:
        if len(msg) == 0:
            return False, 'uno de los mensajes está vacío'
        elif len(msg) > MAX_SMS_LENGTH:
            return False, 'uno de los mensajes supera la longitud maxima: {}'.format(MAX_SMS_LENGTH)
    return True, ''


def get_file_encoding(filepath):
    with open(filepath, 'rb') as file:
        enc = chardet.detect(file.read(1024))
        return enc['encoding'] if 'encoding' in enc else None


# this 'main' only for testing
if __name__ == '__main__':
    run(host='0.0.0.0', port=8082, debug=True)

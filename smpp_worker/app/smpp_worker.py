#!/usr/bin/python3.8
"""
Author  : Gabriel Cuba
Desc    : Connects to SMSC via SMPP and handles requests via ZMQ
"""
import _thread
import math
import os
import signal
from functools import partial
from threading import Thread
import sys
import logging
from sched import scheduler
import time
import json
import smpplib.gsm
import smpplib.client
import smpplib.consts
import smpplib.smpp
import smpplib.exceptions
import zmq
import configparser

_configuration_file = '/app/config/global_config.ini'

# initialize global variables
submit_response_dict = {}
destination_sequence_dict = {}
save_submit = False
controller = None

''' SMPP functions'''


def send_message(smpp_client, dest, sender, part, encoding_flag, msg_type_flag):
    pdu = smpp_client.send_message(
        source_addr_ton=smpplib.consts.SMPP_TON_ALNUM,
        source_addr_npi=smpplib.consts.SMPP_NPI_ISDN,
        source_addr=sender,
        dest_addr_ton=smpplib.consts.SMPP_TON_INTL,
        dest_addr_npi=smpplib.consts.SMPP_NPI_ISDN,
        destination_addr=dest,
        short_message=part,
        data_coding=encoding_flag,
        esm_class=msg_type_flag,
        registered_delivery=False,
    )
    logging.debug(pdu.sequence)
    global destination_sequence_dict
    destination_sequence_dict[pdu.sequence] = dest


def message_sent_function(pdu):
    """
    overrides function of smpplib Client class
    :param pdu:
    :return:
    """
    logging.debug('sent {} {}\n'.format(pdu.sequence, pdu.message_id))
    # only save message id when sending batches
    if save_submit and pdu.message_id:
        global submit_response_dict
        submit_response_dict[pdu.sequence] = pdu.message_id


def instance_client(smpp_config):
    """
    function to create an instance of smpplib client based on configured ip and port
    :return: smpplib client object
    """
    logging.info('Starting client ...')
    client = smpplib.client.Client(smpp_config['SMPP_PEER_IP_ADDRESS'], smpp_config['SMPP_PEER_PORT'])
    # Seteamos el manejo de "mensaje enviado"
    client.set_message_sent_handler(message_sent_function)
    client.set_message_received_handler(lambda pdu: sys.stdout.write('delivered {}\n'.format(pdu.receipted_message_id)))
    client.connect()
    client.bind_transceiver(system_id=smpp_config['SMPP_SYSTEM_ID'],
                            password=smpp_config['SMPP_SYSTEM_PASSWORD'],
                            system_type=smpp_config['SMPP_SYSTEM_TYPE'])
    logging.info('Client started ...')
    return client


def send_enquire_link(smpp_client, enquire_link_timer):
    """
    function thats sends enquire links as long client is up
    :param enquire_link_timer:
    :param smpp_client: smpplib client object in connected state
    :return: nothing
    """
    while True:
        pdu = smpplib.smpp.make_pdu('enquire_link', client=smpp_client)
        try:
            smpp_client.send_pdu(pdu)
        except smpplib.exceptions.PDUError as e:
            logging.error(str(e))
            break
        time.sleep(enquire_link_timer)


'''service functions'''


def get_request_type(dict_data):
    if isinstance(dict_data, dict):
        if 'type' in dict_data:
            if dict_data['type'] in ['batch', 'stop']:
                return dict_data['type']
    return 'invalid'


def start_processing(client, batch_data, message, sender, batch_type):
    """

    :param batch_data:
    :param client:
    :param message:
    :param sender:
    :param batch_type:
    :return:
    """
    global submit_response_dict
    global destination_sequence_dict
    # clean global variables
    submit_response_dict = {}
    destination_sequence_dict = {}

    # definimos parámetros del scheduler
    priority = 2  # priority 1 for adding new events later
    period = 1 / TPS
    # SCHEDULING
    schedule = scheduler(time.time, time.sleep)
    batch_size = len(batch_data)
    if batch_size < 1:
        logging.error('empty batch')
        return
    ''' 
    Set an initial delay in order to prevent queuing of first PDUs at TCP/IP level.
    Currently value is proportional to base e logarithm of list size, but can be changed
    This works fine for lists up to 5 M of subscribers at 900 TPS, as tested
    '''
    initial_delay = round(math.log(batch_size / 100000 + 1) * 6)
    logging.debug('Initial delay: {} seconds'.format(initial_delay))
    if batch_type == '1':
        # use smpplib to build message GSM parts, encoding_flag, and msg_type_flag
        parts, encoding_flag, msg_type_flag = smpplib.gsm.make_parts(message, encoding=ENCODING_SCHEME)
        i = 0
        # double loop for the case of fragmented messages
        for msisdn in batch_data:
            for part in parts:
                delay = initial_delay + (i * period)
                schedule.enter(delay, priority, send_message,
                               (client, msisdn, str(sender), part, encoding_flag, msg_type_flag))
                i += 1
    elif batch_type == '2':
        i = 0
        for msisdn, msg in batch_data.items():
            parts, encoding_flag, msg_type_flag = smpplib.gsm.make_parts(msg, encoding=ENCODING_SCHEME)
            for part in parts:
                delay = initial_delay + (i * period)
                schedule.enter(delay, priority, send_message,
                               (client, msisdn, str(sender), part, encoding_flag, msg_type_flag))
                i += 1
    global save_submit
    save_submit = True
    return schedule, batch_size


def post_processing(batch_size):
    """
    handles callback of schedule.run, send result to http server
    :param batch_size:
    :return:
    """
    # revisar si se obtuvieron todas las confirmaciones, de ser correcto, setear flag save_sumbit
    timeout_start = time.time()
    global save_submit
    processed_num = len(destination_sequence_dict)
    while time.time() < timeout_start + CONFIRMATION_TIMEOUT:
        if len(submit_response_dict) >= processed_num:
            save_submit = False
            logging.info('received all confirmations!!')
            break
        time.sleep(1)
    logging.info('{} SMS were sent'.format(len(submit_response_dict)))
    if save_submit:
        save_submit = False
        logging.warning('received only {} confirmations of {} SMS'.format(len(submit_response_dict), batch_size))

    # procesamos confirmaciones
    confirmation_dict = process_submit_confirmations()
    logging.info('Batch post processing ended')
    return confirmation_dict


'''auxiliary functions'''


def listen_thread_wrapper(threadfunc):
    """
    Wrap listen function in order to handle exceptions and keep it active
    :param threadfunc: function to be run as thread
    :return: wrapper function
    """

    def wrapper():
        while True:
            try:
                threadfunc()
            except smpplib.exceptions.ConnectionError as e:
                logging.error('Connection error, worker needs restarting: {}'.format(str(e)))
                break
            except smpplib.exceptions.PDUError as e:
                logging.error('Error with SMPP PDU: {}'.format(str(e)))
            except BaseException as e:
                logging.warning('{!r}, restarting thread'.format(e))
                time.sleep(1)
            else:
                logging.warning('exited normally, restarting bad thread')
        logging.error('Listening thread has ended')
        os.kill(os.getpid(), signal.SIGINT)

    return wrapper


def process_submit_confirmations():
    """
    check received confirmations for each destination in global list "destination_sequence_dict"
    :return: confirmation_dict with msisdn-result pairs
    """
    confirmation_dict = {}
    for seq, msisdn in destination_sequence_dict.items():
        if seq in submit_response_dict:
            # confirmation_dict[msisdn] = submit_response_dict[seq]
            confirmation_dict[msisdn] = 'OK'
            logging.debug(seq)
        else:
            confirmation_dict[msisdn] = 'NOOK'
            logging.debug(msisdn)
    return confirmation_dict


def load_configuration():
    """
    load the configuration file
    :return: True if no error
    """
    global _configuration_file
    if (len(sys.argv) == 3) and sys.argv[1] == '--conf':
        _global_configuration_file = sys.argv[2]
    _worker_group = os.getenv('SMPP_WORKER_GROUP')
    global_config = configparser.ConfigParser()
    global_config.read(_configuration_file)
    worker_config = global_config[_worker_group]
    global ENCODING_SCHEME
    ENCODING_SCHEME = getattr(smpplib.consts, global_config['smpp'].get('ENCODING_SCHEME', 'SMPP_ENCODING_DEFAULT'))
    global TPS
    TPS = float(worker_config['MAX_TPS'])
    global CONFIRMATION_TIMEOUT
    CONFIRMATION_TIMEOUT = int(global_config['smpp']['CONFIRMATION_TIMEOUT'])
    return global_config, worker_config


def set_logging(config):
    """
    Sets the logging, based on configuration file parameters
    :return:
    """
    log = logging.getLogger()
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(log_formatter)
    numeric_level = getattr(logging, config['LOG_LEVEL'].upper(), 20)
    handler.setLevel(numeric_level)
    log.addHandler(handler)
    log.setLevel(numeric_level)


'''ZMQ methods'''


def listen_controller():
    global controller
    controller_poller = zmq.Poller()
    controller_poller.register(controller, zmq.POLLIN)
    while True:
        logging.info('polling controller')
        try:
            if controller_poller.poll():
                logging.info('received STOP from controller')
                if not save_submit:
                    logging.error('There is no active batch')
                    continue
                logging.info('Batch stopped successfully')
                break
        except zmq.error.ZMQError as e:
            logging.error('controller is not active' + str(e))
            break
    _thread.interrupt_main()


def init_controller(context, controller_pubsub_ip, controller_pubsub_port):
    global controller
    controller = context.socket(zmq.SUB)
    controller.setsockopt(zmq.LINGER, 0)
    controller.connect("tcp://{}:{}".format(controller_pubsub_ip, controller_pubsub_port))


def worker(receiver, sender, client):
    logging.info('entering working thread')
    receive_poller = zmq.Poller()
    receive_poller.register(receiver, zmq.POLLIN)
    sender_poller = zmq.Poller()
    sender_poller.register(sender, zmq.POLLOUT)
    while not receiver.closed:
        logging.info('polling jobs')
        # outer try-except to catch poller.poll zmq exceptions
        try:
            if receive_poller.poll():
                do_the_work(sender_poller, receiver, sender, client)
        except zmq.error.ZMQError as e:
            logging.error(str(e))
            break


def do_the_work(sender_poller, receiver, sender, client):
    global controller
    payload = json.loads(receiver.recv_json())
    batch_id = str(payload['batch_id'])
    chunk_id = str(payload['chunk_id'])
    logging.info('Received job: id={}, chunk={}'.format(batch_id, chunk_id))
    controller.setsockopt_string(zmq.SUBSCRIBE, batch_id)
    schedule, batch_size = start_processing(client, payload['data'], payload['message'], payload['sender'],
                                            payload['batch_type'])
    status = True
    try:
        schedule.run()
        logging.info('Campaign sent successfully, checking confirmations ...')
    except Exception as error:
        logging.error('Some error occurred and batch process was interrupted: {}'.format(error))
        status = False
    finally:
        controller.setsockopt_string(zmq.UNSUBSCRIBE, batch_id)
        resp_dict = post_processing(batch_size)
        if sender_poller.poll(1000):
            sender.send_json(
                json.dumps({'batch_id': batch_id, 'chunk_id': chunk_id, 'status': status,
                            'result_list': resp_dict}))


def main():
    """
    Main functions that initiates threads and executes main loop
    :return:
    """

    def handle_exit(*args):
        logging.info('disconnecting ...')
        client.disconnect()
        logging.info('SMPP disconnected, the program will exit now')
        receiver.close()
        working_thread.join()
        sender.close()
        global controller
        controller.close()
        context.term()
        sys.exit(1)

    # load configuration and set logging
    try:
        global_config, worker_config = load_configuration()
    except configparser.Error as e:
        logging.error('unable to load configuration: {}'.format(str(e)))
        sys.exit(1)
    set_logging(worker_config)
    # initialize SMPP client
    logging.info('Service start')
    try:
        client = instance_client(global_config['smpp'])
    except smpplib.exceptions.PDUError as e:
        logging.error('Received error from SMSC: {}'.format(str(e)))
        sys.exit(1)
    except smpplib.exceptions.ConnectionError:
        logging.error('cant connect with SMSC')
        sys.exit(1)
    # listen to SMPP messages in another thread
    logging.info('Sending SMPP listening to another thread')
    t_listen = Thread(target=listen_thread_wrapper(client.listen), daemon=True)
    t_listen.start()
    # send periodic enquire_link in another thread
    if global_config.getboolean('smpp', 'AUTO_ENQUIRE_LINK'):
        logging.info('Sending periodic enquire link to another thread')
        t_heartbeat = Thread(target=send_enquire_link, args=(client, int(global_config['smpp']['ENQUIRE_LINK_TIMER']),),
                             daemon=True)
        t_heartbeat.start()

    # initialize ZMQ
    load_balancer_ip = global_config['front_end']['load_balancer_ip']
    context = zmq.Context()
    # Socket to receive messages on
    receiver = context.socket(zmq.PULL)
    receiver.setsockopt(zmq.LINGER, 1000)
    receiver.connect("tcp://{}:{}".format(load_balancer_ip, worker_config['ventilator_push_port']))
    # Socket to send messages to
    sender = context.socket(zmq.PUSH)
    sender.setsockopt(zmq.LINGER, 1000)
    sender.connect("tcp://{}:{}".format(load_balancer_ip, worker_config['sink_push_port']))
    # start controller_listener
    init_controller(context, load_balancer_ip, worker_config['controller_pubsub_port'])
    # wait for messages in another thread
    working_thread = Thread(target=worker, args=(receiver, sender, client), daemon=False)
    working_thread.start()
    # handle exit events so we can cleanup
    signal.signal(signal.SIGTERM, partial(handle_exit, client, context, working_thread, receiver, sender, ))
    signal.signal(signal.SIGINT, partial(handle_exit, client, context, working_thread, receiver, sender, ))
    # notify http server that we exist
    try:
        sender.send_json('{}', zmq.NOBLOCK)
    except zmq.ZMQError as e:
        logging.error(str(e))
    listen_controller()


if __name__ == "__main__":
    main()

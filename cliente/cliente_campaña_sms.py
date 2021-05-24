from argparse import ArgumentParser
import requests
from re import findall
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import sys

ipaddress = '10.30.7.109'
port = '443'
__version__ = '1.5'
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


def send_batch(message, filepath, user, sender, priority):
    url = 'https://{}:{}/api/v2/sms_batches'.format(ipaddress, port)
    values = {"user": user, "message": message, "sender": sender, "priority": priority}
    try:
        with open(filepath, 'rb') as file:
            files = {'batch_file': file}
            response = requests.post(url, data=values, files=files, verify=False)
    except FileNotFoundError:
        print('Error: el archivo especificado no existe')
        return
    except requests.exceptions.ConnectionError:
        print('Error: No se puede alcanzar el servidor')
        return
    if response.status_code == 202:
        data = response.json()
        idx = data['id']
        print('SOLICITUD RECIBIDA: el ID del batch es {}, para revisar el status utilice el comando:'.format(idx))
        print('cliente.exe -c query -i {}'.format(idx))
    else:
        data = response.json()
        print('ERROR: {}'.format(data['message']))


def query_batch(batch_id, priority):
    url = 'https://{}:{}/api/v2/sms_batches/{}/{}/status'.format(ipaddress, port, priority, batch_id)
    response = requests.get(url, verify=False)
    if response.status_code == 200:
        data = response.json()
        print('Status: {}, Progress: {}'.format(data['status'], data['progress']))
    else:
        print('Error: {}'.format(response.json()['message']))


def download_file(batch_id, priority):
    url = 'https://{}:{}/api/v2/sms_batches/{}/{}/report'.format(ipaddress, port, priority, batch_id)
    response = requests.get(url, verify=False)
    if response.status_code == 200:
        header = response.headers['content-disposition']
        filename = findall("filename=(.+)", header)[0].strip('"')
        with open(filename, 'wb') as f:
            f.write(response.content)
        print('Reporte descargado exitosamente')
    else:
        print('Error: {}'.format(response.json()['message']))


def stop_batch(batch_id, priority):
    url = 'https://{}:{}/api/v2/sms_batches/{}/{}'.format(ipaddress, port, priority, batch_id)
    response = requests.delete(url, verify=False)
    if response.status_code == 200:
        print('Batch detenido exitosamente')
    else:
        print('Error: {}'.format(response.json()['message']))


def main():
    parser = ArgumentParser()
    parser.add_argument('-c', '--comando', default='ejecutar', help="comando")
    parser.add_argument('-i', '--id', default=None, help="id del batch")
    parser.add_argument('-f', '--filepath', default=None, help="Absolute path of msisdn path")
    parser.add_argument('-m', '--mensaje', default=None, help="texto del SMS")
    parser.add_argument('-u', '--user', default=None, help="usuario")
    parser.add_argument('-s', '--sender', default=None, help="origen")
    parser.add_argument('-p', '--priority', default='normal', help="prioridad del batch")
    parser.add_argument('-v', '--version', action='version', version='Version del cliente: {}'.format(__version__))
    args = parser.parse_args()

    comando = args.comando
    filepath = args.filepath
    message = args.mensaje
    batch_id = args.id
    user = args.user
    priority = args.priority
    sender = args.sender

    if len(sys.argv) == 1:
        menu()
        return
    if comando == 'ejecutar':
        if message and filepath and sender:
            send_batch(message, filepath, user, sender, priority)
        else:
            print('Para ejecutar una campaña especifique el archivo input, mensaje, TPS, usuario y Origen, '
                  'por ejemplo: '
                  '\n\ncliente.exe -f "file.txt" -m "Este es mi mensaje" -u "user" -s "NOC"')
            return
    elif comando == 'query':
        if batch_id:
            query_batch(batch_id, priority)
        else:
            print("Especifique el ID del batch que desea consultar")
            return
    elif comando == 'download':
        if batch_id:
            download_file(batch_id, priority)
        else:
            print("Especifique el ID del batch que desea descargar")
            return
    elif comando == 'stop':
        if batch_id:
            stop_batch(batch_id, priority)
        else:
            print("Especifique el ID del batch que desea descargar")
    else:
        print("Especifique el comando: 'ejecutar', 'query' o 'reporte'")


def menu():
    while True:
        print('\n### Envío de SMS masivos ###')
        print('1 - Enviar nueva campaña\n2 - Consultar estado de campaña\n3 - Detener campaña\n4 - Descargar '
              'resultado\n5 - Salir\n')
        opcion = input('seleccione una opción\n')
        if opcion == '1':
            filepath = input('Ingrese el nombre del archivo. Si no está en la misma ruta, especifique la ruta '
                             'completa:\n')
            sender = input('Ingrese el nombre o numero orígen:\n')
            message = input('Ingrese el mensaje que se enviará. Si el mensaje está especificado en el archivo, '
                            'deje en blanco y presione enter\n')
            user = input('Ingrese el usuario:\n')
            priority = input('Ingrese la prioridad, para usar la prioridad normal deje en blanco y presione Enter\n')
            priority = 'normal' if priority == '' else priority
            send_batch(message, filepath, user, sender, priority)
        elif opcion in ['2', '3', '4']:
            batch_id = input('Ingrese el ID del batch:\n')
            priority = input('Ingrese la prioridad del batch. Si no fue especificada, deje en blanco:\n')
            priority = 'normal' if priority == '' else priority
            if opcion == '2':
                query_batch(batch_id, priority)
            elif opcion == '3':
                stop_batch(batch_id, priority)
            elif opcion == '4':
                download_file(batch_id, priority)
        elif opcion == '5':
            break
        else:
            print('opcion inválida\n')


if __name__ == '__main__':
    main()

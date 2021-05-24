import socket
import fcntl
import struct
import configparser
from os import path

base_path = path.dirname(path.abspath(__file__))
allowed_networks = ('10.30.17.', '10.30.7.')
allowed_if_names = ('veth', 'lo', 'br', 'docker')

def get_ip_address():
    for ix in socket.if_nameindex():
        name = ix[1]
        if name.startswith(allowed_if_names):
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', name.encode("UTF-8")))[20:24])
        if addr.startswith(allowed_networks):
            print('Se utilizará la IP {}'.format(addr))
            return addr
    print('No es posible encontrar una dirección IP adecuada')


if __name__ == '__main__':
    config_file = base_path + '/cluster_config/global_config.ini'
    ip_addr = get_ip_address()
    config = configparser.ConfigParser()
    config.read(config_file)
    config.set("front_end", "load_balancer_ip", ip_addr)
    with open(config_file, "w") as f:
        config.write(f)

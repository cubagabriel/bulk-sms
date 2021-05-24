import csv
import os
import unittest
import requests
from time import sleep
from re import findall
import configparser

current_path = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config.read(current_path + '/../cluster_config/global_config.ini')
ipaddress = config['front_end']['load_balancer_ip']
port = '442'


def send_batch():
    priority = 'normal'
    url = 'https://{}:{}/api/v2/sms_batches'.format(ipaddress, port)
    files = {'batch_file': open(current_path + '/../test_data/test_cicd.txt', 'rb')}
    values = {"user": 'INGENIERIA', "message": 'mensaje de prueba', "sender": 'cicd', "priority": priority}
    response = requests.post(url, data=values, files=files, verify=False)
    return int(response.json()['id'])


def query_batch(batch_id):
    priority = 'normal'
    url = 'https://{}:{}/api/v2/sms_batches/{}/{}/status'.format(ipaddress, port, priority, batch_id)
    response = requests.get(url, verify=False)
    return response.json()


class TestAll(unittest.TestCase):
    received_batch_id = None
    filepath = ''

    def test_01_send_batch(self):
        self.__class__.received_batch_id = send_batch()
        # wait for execution
        sleep(100)
        data = query_batch(self.received_batch_id)
        self.assertEqual(data['status'], 'completed')

    def test_02_download_batch(self):
        url = 'https://{}:{}/api/v2/sms_batches/{}/{}/report'.format(ipaddress, port, 'normal', self.received_batch_id)
        response = requests.get(url, verify=False)
        self.assertIn("filename=", response.headers.get('Content-Disposition'))
        header = response.headers['content-disposition']
        filename = findall("filename=(.+)", header)[0].strip('"')
        self.__class__.filepath = os.path.join(current_path, filename)
        print(self.filepath)
        with open(self.filepath, 'wb') as f:
            f.write(response.content)

    def test_03_batch_ok(self):
        # open file and check that all were OK
        print(self.filepath)
        with open(self.filepath, 'r', encoding='utf8') as file:
            csv_reader = csv.reader(file, delimiter=',')
            flag = True
            for row in csv_reader:
                if row[1] != 'OK':
                    flag = False
                    break
            self.assertEqual(flag, True)


class TestStop(unittest.TestCase):
    received_batch_id = None

    def test_01_sendbatch(self):
        self.received_batch_id = send_batch()
        url = 'https://{}:{}/api/v2/sms_batches/{}/{}'.format(ipaddress, port, 'normal', self.received_batch_id)
        # wait and stop
        sleep(2)
        requests.delete(url, verify=False)
        data = query_batch(self.received_batch_id)
        self.assertEqual(data['status'], 'stopped')


if __name__ == '__main__':
    unittest.main(failfast=True)

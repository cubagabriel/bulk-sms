#!/usr/bin/python3.8
"""
Author  : Gabriel Cuba
Desc    : Distributes workload across the smpp workers
"""
import csv
import json
import time
from itertools import product, islice
import random
from threading import Thread
import zmq
import queue
from datetime import datetime
import logging


logger = logging.getLogger(__name__)
RESULT_PATH = ''


class Job:
    STARTING = 'starting'
    STOPPED = 'stopped'
    FAILED = 'failed'
    INPROGRESS = 'inprogress'
    ASSIGNED = 'assigned'
    COMPLETED = 'completed'

    def __init__(self, identifier, file_name, message, batch_type, sender, tps_worker, worker_time):
        self.id = identifier
        self.file_name = file_name
        self.date = datetime.strftime(datetime.now(), "%Y%m%d%H%M%S")
        self.result_file_name = RESULT_PATH + '/result_{}_{}.txt'.format(identifier, self.date)
        self.status = self.STARTING
        self.size = get_file_size(self.file_name)
        self.message = message
        # this queue is thread safe
        self.queue, self.chunk_num = build_queue(self.size, tps_worker * worker_time)
        self.type = batch_type
        self.sender = sender
        # only for monitor purposes
        self.chunk_monitor = dict()
        self.completed_chunk = list()
        logger.info('job {} created, total chunks: {} '.format(self.id, self.chunk_num))

    def serve(self):
        if self.status == self.STARTING:
            self.status = self.INPROGRESS
        item = self.queue.get(block=False)
        chunk_id = item['chunk_id']
        logger.debug('serving job id {}, chunk {}'.format(str(self.id), str(chunk_id)))
        self.chunk_monitor[int(chunk_id)] = datetime.now()
        if chunk_id == self.chunk_num - 1:
            self.status = self.ASSIGNED
        data = read_part_of_file(self.file_name, item['start'], item['end'], self.type)
        return data, self.id, self.message, self.type, self.sender, chunk_id

    def get_progress(self):
        if self.status == self.COMPLETED:
            return 1.0
        elif self.status == self.STARTING:
            return 0.0
        else:
            return len(self.completed_chunk) / self.chunk_num

    def update_result(self, chunk_id, result_list):
        # thread-safe, only result manager thread can update result
        update_result_file(self.result_file_name, result_list)
        self.queue.task_done()
        del self.chunk_monitor[chunk_id]
        self.completed_chunk.append(chunk_id)
        if len(self.completed_chunk) == self.chunk_num:
            logger.info('JOB {} COMPLETED '.format(str(self.id)))
            self.status = self.COMPLETED


class LoadBalancer:
    def __init__(self, max_workers, tps_per_worker, time_per_worker, expired_factor, sender_socket_port,
                 stop_socket_port, receiver_socket_port, result_path):
        global RESULT_PATH
        RESULT_PATH = result_path
        self.MAX_WORKERS = int(max_workers)
        self.WORKER_TPS = int(tps_per_worker)
        self.WORKER_TIME = int(time_per_worker)
        self.EXPIRED_FACTOR = float(expired_factor)
        self.EXPIRED_JOBS_INTERVAL = self.WORKER_TIME * 1.1
        self.jobs = dict()
        self.zmq_context = zmq.Context()
        logger.info('starting result manager thread')
        self.manager_thread = Thread(target=self.result_manager, args=(receiver_socket_port,),
                                     daemon=True)
        self.manager_thread.start()
        self.sender_socket, self.sender_poller, self.stop_socket = start_zmq_sockets(self.zmq_context,
                                                                                     sender_socket_port,
                                                                                     stop_socket_port)
        Thread(target=self.check_expired_jobs, daemon=True).start()

    def assign(self, new_job_flag=False):
        job_keys = list(self.get_jobs_tobe_assigned())
        if new_job_flag:
            if len(job_keys) == 1:
                # the new job is the only one
                num_worker = self.MAX_WORKERS
            else:
                # there are other jobs waiting so workers are busy
                logger.info('all workers are bussy, will asign later')
                return
        else:
            num_worker = self.MAX_WORKERS if len(job_keys) == 0 else 1
        num_queues = len(job_keys)
        logger.info('num_worker: {}, num_queues: {}'.format(num_worker, num_queues))
        if num_queues == 0 or num_worker == 0:
            logger.info('no workers or queues to assign')
            return
        if num_worker <= num_queues:
            selection = random.sample(job_keys, num_worker)
        else:
            selection = random.choice(list(combinations_with_replacement(job_keys, num_worker)))
        for job_key in selection:
            logger.debug('sending json to worker, job {}'.format(job_key))
            try:
                if self.sender_poller.poll(5000):
                    self.sender_socket.send_json(prepare_json(self.jobs[job_key], self.WORKER_TPS))
                else:
                    logger.error('No workers available, will try later')
                    # self.jobs[job_key].status = Job.FAILED
            except queue.Empty:
                logger.warning('job {} queue is already empty, nothing to do'.format(job_key))
            except zmq.error.ZMQError as e:
                logger.error('ZMQ error: {}'.format(str(e)))

    def get_jobs_tobe_assigned(self):
        for idx, job in self.jobs.items():
            if job.status == Job.INPROGRESS or job.status == Job.STARTING:
                yield idx

    def get_active_jobs(self):
        for idx, job in self.jobs.items():
            if job.status == Job.INPROGRESS or job.status == Job.ASSIGNED:
                yield idx

    def add_new_job(self, identifier, file_name, message, batch_type, sender):
        self.jobs[identifier] = Job(identifier, file_name, message, batch_type, sender, self.WORKER_TPS,
                                    self.WORKER_TIME)

    def update_job(self, json_object):
        batch_id = int(json_object['batch_id'])
        result_list = json_object['result_list']
        chunk_id = int(json_object['chunk_id'])
        logger.info('updating job: {}, chunk: {}'.format(batch_id, chunk_id))
        try:
            self.jobs[batch_id].update_result(chunk_id, result_list)
        except KeyError:
            logger.error('cant update job, does not exist')

    def stop_job(self, job_id):
        logger.warning('stoping job {}'.format(job_id))
        self.jobs[job_id].status = Job.STOPPED
        self.stop_socket.send_string(str(job_id))

    def check_expired_jobs(self):
        while True:
            time.sleep(self.EXPIRED_JOBS_INTERVAL)
            job_keys = list(self.get_active_jobs())
            for job_key in job_keys:
                job = self.jobs[job_key]
                for chunk_id, startime in job.chunk_monitor.items():
                    if (datetime.now() - startime).total_seconds() > self.WORKER_TIME * self.EXPIRED_FACTOR:
                        job.status = Job.FAILED
                        logger.error('JOB FAILED: {} chunk: {}, started on {}'.format(job_key, chunk_id, startime))

    def result_manager(self, receiver_socket_port):
        # Socket for receiving messages
        receiver = self.zmq_context.socket(zmq.PULL)
        logging.info("listening results at tcp://*:{}".format(receiver_socket_port))
        receiver.bind("tcp://0.0.0.0:{}".format(receiver_socket_port))
        while True:
            try:
                json_object = json.loads(receiver.recv_json())
            except zmq.error.ZMQError as e:
                logger.error(str(e))
                break
            if json_object:
                self.update_job(json_object)
                if not json_object['status']:
                    logger.warning('execution of this chunk was interrupted, wont assign new chunk')
                    continue
            else:
                logger.info('new worker alive, assign jobs')
            self.assign()
        receiver.close()

    def terminate(self):
        logger.info('closing sockets, context and threads')
        self.sender_socket.close()
        self.stop_socket.close()
        self.zmq_context.term()
        self.manager_thread.join()


# file handling code #


def update_result_file(result_file, result_list):
    with open(result_file, 'a', newline='') as csv_file:
        writer = csv.writer(csv_file, quoting=csv.QUOTE_NONE, delimiter=',', quotechar='')
        for key, value in result_list.items():
            writer.writerow([key, value])


def read_part_of_file(file_name, first_line, last_line, batch_type):
    with open(file_name, 'r') as file:
        for i in range(first_line):
            next(file)
        if batch_type == '1':
            data = []
            for i in range(last_line - first_line):
                data.append(next(file).rstrip())
        elif batch_type == '2':
            data = {}
            csv_reader = csv.reader(file)
            for row in islice(csv_reader, first_line, last_line):
                data[row[0]] = row[1]
        return data


def combinations_with_replacement(iterable, r):
    pool = tuple(iterable)
    n = len(pool)
    unique_values = len(iterable)
    for indices in product(range(n), repeat=r):
        if sorted(indices) == list(indices) and len(set(indices)) == unique_values:
            yield tuple(pool[i] for i in indices)


def get_file_size(filename):
    with open(filename) as f:
        return sum(1 for _ in f)


def build_queue(file_size, chunk_size):
    chunk_num = file_size // chunk_size
    last_chunk = file_size % chunk_size
    q = queue.Queue()
    count = 0
    for i in range(chunk_num):
        q.put({'chunk_id': i, 'start': i * chunk_size, 'end': (i + 1) * chunk_size})
        count += 1
    if last_chunk != 0:
        q.put({'chunk_id': chunk_num, 'start': chunk_num * chunk_size, 'end': file_size})
        count += 1
    return q, count


def prepare_json(item, tps_worker):
    data, identifier, message, batch_type, sender, chunk_id = item.serve()
    logger.info('Serving job={}, chunk={}'.format(identifier, chunk_id))
    dict_data = {'message': str(message), 'tps': tps_worker, 'batch_id': identifier,
                 'sender': sender, 'batch_type': batch_type, 'data': data, 'chunk_id': chunk_id}
    json_object = json.dumps(dict_data)
    return json_object


def start_zmq_sockets(zmq_context, sender_socket_port, stop_socket_port):
    logger.info('starting sender socket')
    sender_socket = zmq_context.socket(zmq.PUSH)
    sender_socket.setsockopt(zmq.LINGER, 1000)
    sender_poller = zmq.Poller()
    sender_poller.register(sender_socket, zmq.POLLOUT)
    sender_socket.bind("tcp://*:{}".format(sender_socket_port))
    logger.info('starting stopper socket')
    stop_socket = zmq_context.socket(zmq.PUB)
    stop_socket.bind("tcp://0.0.0.0:{}".format(stop_socket_port))
    return sender_socket, sender_poller, stop_socket

# -*- coding: utf-8 -*-
#  Copyright (c) 2019 - now, Eggroll Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import os
import sys
import threading
import uuid
from concurrent.futures import wait, FIRST_EXCEPTION
from threading import Thread

from eggroll.core.client import CommandClient
from eggroll.core.command.command_model import CommandURI
from eggroll.core.conf_keys import SessionConfKeys
from eggroll.core.constants import StoreTypes, SerdesTypes, PartitionerTypes
from eggroll.core.datastructure.broker import FifoBroker
from eggroll.core.io.kv_adapter import LmdbSortedKvAdapter, \
    RocksdbSortedKvAdapter
from eggroll.core.meta_model import ErStoreLocator, ErJob, ErStore, ErFunctor, \
    ErTask, ErPair, ErPartition
from eggroll.core.pair_store.adapter import BrokerAdapter
from eggroll.core.serdes import cloudpickle
from eggroll.core.serdes.eggroll_serdes import PickleSerdes, CloudPickleSerdes, \
    EmptySerdes
from eggroll.core.session import ErSession
from eggroll.core.utils import generate_job_id, generate_task_id
from eggroll.core.utils import string_to_bytes, hash_code
from eggroll.roll_pair import create_serdes
from eggroll.roll_pair.transfer_pair import TransferPair, BatchBroker
from eggroll.roll_pair.utils.gc_utils import Recorder
from eggroll.roll_pair.utils.pair_utils import partitioner
from eggroll.utils import log_utils

LOGGER = log_utils.get_logger()


def runtime_init(session: ErSession):
    rps = RollPairContext(session=session)
    rps.set_session_table_recorder()
    return rps


class RollPairContext(object):

    def __init__(self, session: ErSession):
        self.__session = session
        self.session_id = session.get_session_id()
        self.default_store_type = StoreTypes.ROLLPAIR_LMDB
        self.default_store_serdes = SerdesTypes.PICKLE
        self.deploy_mode = session.get_option(SessionConfKeys.CONFKEY_SESSION_DEPLOY_MODE)
        self.__session_meta = session.get_session_meta()

    def set_store_type(self, store_type: str):
        self.default_store_type = store_type

    def set_store_serdes(self, serdes_type: str):
        self.default_store_serdes = serdes_type

    def set_session_table_recorder(self):
        self.__session.set_table_recorder(self)

    def get_session(self):
        return self.__session

    def get_roll(self):
        return self.__session._rolls[0]

    def route_to_egg(self, partition: ErPartition):
        return self.__session.route_to_egg(partition)

    def populate_processor(self, store: ErStore):
        populated_partitions = list()
        for p in store._partitions:
            pp = ErPartition(id=p._id, store_locator=p._store_locator, processor=self.route_to_egg(p))
            populated_partitions.append(pp)
        return ErStore(store_locator=store._store_locator, partitions=populated_partitions, options=store._options)

    def load(self, namespace=None, name=None, options={}):
        store_type = options.get('store_type', self.default_store_type)
        total_partitions = options.get('total_partitions', 1)
        partitioner = options.get('partitioner', PartitionerTypes.BYTESTRING_HASH)
        store_serdes = options.get('serdes', self.default_store_serdes)
        create_if_missing = options.get('create_if_missing', True)
        # todo:1: add combine options to pass it through
        store_options = self.__session.get_all_options()
        store_options.update(options)
        final_options = store_options.copy()
        # TODO:1: tostring in er model
        if 'create_if_missing' in final_options:
            del final_options['create_if_missing']
        if 'include_key' in final_options:
            del final_options['include_key']
        if 'total_partitions' in final_options:
            del final_options['total_partitions']
        if 'name' in final_options:
            del final_options['name']
        if 'namespace' in final_options:
            del final_options['namespace']
        if 'keys_only' in final_options:
            del final_options['keys_only']
        LOGGER.info("final_options:{}".format(final_options))
        store = ErStore(
                store_locator=ErStoreLocator(
                        store_type=store_type,
                        namespace=namespace,
                        name=name,
                        total_partitions=total_partitions,
                        partitioner=partitioner,
                        serdes=store_serdes),
                options=final_options)

        if create_if_missing:
            result = self.__session._cluster_manager_client.get_or_create_store(store)
        else:
            result = self.__session._cluster_manager_client.get_store(store)
            if result is None:
                raise EnvironmentError(
                        "result is None, please check whether the store:{} has been created before".format(store))

        return RollPair(self.populate_processor(result), self)

    def parallelize(self, data, options={}):
        namespace = options.get("namespace", None)
        name = options.get("name", None)
        create_if_missing = options.get("create_if_missing", True)

        if namespace is None:
            namespace = self.session_id
        if name is None:
            name = str(uuid.uuid1())
        store = self.load(namespace=namespace, name=name, options=options)
        return store.put_all(data, options=options)

    def cleanup(self, namespace, name, options={}):
        pass


def default_partitioner(k):
    return 0


def default_egg_router(k):
    return 0


class RollPair(object):
    ROLL_PAIR_URI_PREFIX = 'v1/roll-pair'
    EGG_PAIR_URI_PREFIX = 'v1/egg-pair'

    RUN_JOB = 'runJob'
    RUN_TASK = 'runTask'

    AGGREGATE = 'aggregate'
    COLLAPSE_PARTITIONS = 'collapsePartitions'
    COUNT = 'count'
    DELETE = "delete"
    DESTROY = "destroy"
    FILTER = 'filter'
    FLAT_MAP = 'flatMap'
    GET = "get"
    GET_ALL = "getAll"
    GLOM = 'glom'
    JOIN = 'join'
    MAP = 'map'
    MAP_PARTITIONS = 'mapPartitions'
    MAP_VALUES = 'mapValues'
    PUT = "put"
    PUT_ALL = "putAll"
    REDUCE = 'reduce'
    SAMPLE = 'sample'
    SUBTRACT_BY_KEY = 'subtractByKey'
    UNION = 'union'

    def __init__(self, er_store: ErStore, rp_ctx: RollPairContext):
        self.__store = er_store
        self.ctx = rp_ctx
        self.__command_serdes = SerdesTypes.PROTOBUF
        self.__roll_pair_master = self.ctx.get_roll()
        self.__command_client = CommandClient()
        self.value_serdes = self.get_store_serdes()
        self.key_serdes = self.get_store_serdes()
        self.partitioner = partitioner(hash_code, self.__store._store_locator._total_partitions)
        self.egg_router = default_egg_router
        self.__session_id = self.ctx.session_id
        self.gc_enable = True
        self.recorder = Recorder(er_store=er_store, rpc=rp_ctx)
        self.recorder.record()

    def __del__(self):
        if not self.gc_enable:
            return
        if self.recorder.check_table_deletable():
            self.recorder.delete_record()
            self.destroy()
            LOGGER.debug("process {} thread {} run {} del table name:{}, namespace:{}".
                         format(os.getpid(), threading.currentThread().ident,
                                sys._getframe().f_code.co_name,
                                self.__store._store_locator._name,
                                self.__store._store_locator._namespace))

    def __repr__(self):
        return f'python RollPair(_store={self.__store})'

    def set_gc_enable(self):
        self.gc_enable = True

    def set_gc_disable(self):
        self.gc_enable = False

    def __get_unary_input_adapter(self, options={}):
        input_adapter = None
        if self.ctx.default_store_type == StoreTypes.ROLLPAIR_LMDB:
            input_adapter = LmdbSortedKvAdapter(options)
        elif self.ctx.default_store_type == StoreTypes.ROLLPAIR_LEVELDB:
            input_adapter = RocksdbSortedKvAdapter(options)
        return input_adapter

    def get_store_serdes(self):
        serdes_type = self.__store._store_locator._serdes
        LOGGER.info(f'serdes type: {serdes_type}')
        if serdes_type == SerdesTypes.CLOUD_PICKLE or serdes_type == SerdesTypes.PROTOBUF:
            return CloudPickleSerdes
        elif serdes_type == SerdesTypes.PICKLE:
            return PickleSerdes
        else:
            return EmptySerdes

    def get_partitions(self):
        return self.__store._store_locator._total_partitions

    def get_name(self):
        return self.__store._store_locator._name

    def get_namespace(self):
        return self.__store._store_locator._namespace

    def get_store_type(self):
        return self.__store._store_locator._store_type

    def kv_to_bytes(self, **kwargs):
        use_serialize = kwargs.get("use_serialize", True)
        # can not use is None
        if "k" in kwargs and "v" in kwargs:
            k, v = kwargs["k"], kwargs["v"]
            return (self.value_serdes.serialize(k), self.value_serdes.serialize(v)) if use_serialize \
                else (string_to_bytes(k), string_to_bytes(v))
        elif "k" in kwargs:
            k = kwargs["k"]
            return self.value_serdes.serialize(k) if use_serialize else string_to_bytes(k)
        elif "v" in kwargs:
            v = kwargs["v"]
            return self.value_serdes.serialize(v) if use_serialize else string_to_bytes(v)

    """
    
      storage api
    
    """
    def get(self, k, options={}):
        k = create_serdes(self.__store._store_locator._serdes).serialize(k)
        er_pair = ErPair(key=k, value=None)
        outputs = []
        value = None
        partition_id = self.partitioner(k)
        egg = self.ctx.route_to_egg(self.__store._partitions[partition_id])
        LOGGER.info(egg._command_endpoint)
        LOGGER.info(f"count:{self.__store._store_locator._total_partitions}")
        inputs = [ErPartition(id=partition_id, store_locator=self.__store._store_locator)]
        output = [ErPartition(id=partition_id, store_locator=self.__store._store_locator)]

        job_id = generate_job_id(self.__session_id, RollPair.GET)
        job = ErJob(id=job_id,
                    name=RollPair.GET,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[ErFunctor(body=cloudpickle.dumps(er_pair))])

        task = ErTask(id=generate_task_id(job_id, partition_id),
                      name=RollPair.GET,
                      inputs=inputs,
                      outputs=output,
                      job=job)
        LOGGER.info("start send req")
        job_resp = self.__command_client.simple_sync_send(
                input=task,
                output_type=ErPair,
                endpoint=egg._command_endpoint,
                command_uri=CommandURI(f'{RollPair.EGG_PAIR_URI_PREFIX}/{RollPair.RUN_TASK}'),
                serdes_type=self.__command_serdes
        )
        LOGGER.info("get resp:{}".format((job_resp._value)))

        value = self.value_serdes.deserialize(job_resp._value)

        return value

    def put(self, k, v, options={}):
        k, v = create_serdes(self.__store._store_locator._serdes).serialize(k), \
               create_serdes(self.__store._store_locator._serdes).serialize(v)
        er_pair = ErPair(key=k, value=v)
        outputs = []
        partition_id = self.partitioner(k)
        egg = self.ctx.route_to_egg(self.__store._partitions[partition_id])
        inputs = [ErPartition(id=partition_id, store_locator=self.__store._store_locator)]
        output = [ErPartition(id=0, store_locator=self.__store._store_locator)]

        job_id = generate_job_id(self.__session_id, RollPair.PUT)
        job = ErJob(id=job_id,
                    name=RollPair.PUT,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[ErFunctor(body=cloudpickle.dumps(er_pair))])

        task = ErTask(id=generate_task_id(job_id, partition_id),
                      name=RollPair.PUT,
                      inputs=inputs,
                      outputs=output,
                      job=job)
        LOGGER.info("start send req")
        job_resp = self.__command_client.simple_sync_send(
                input=task,
                output_type=ErPair,
                endpoint=egg._command_endpoint,
                command_uri=CommandURI(f'{RollPair.EGG_PAIR_URI_PREFIX}/{RollPair.RUN_TASK}'),
                serdes_type=self.__command_serdes
        )
        LOGGER.info("get resp:{}".format((job_resp._value)))
        value = job_resp._value
        return value

    def get_all(self, options={}):
        LOGGER.info('get all functor')
        job_id = generate_job_id(self.__session_id, RollPair.GET_ALL)
        def send_command():
            job = ErJob(id=job_id,
                        name=RollPair.GET_ALL,
                        inputs=[self.__store],
                        outputs=[self.__store],
                        functors=[])

            result = self.__command_client.simple_sync_send(
                    input=job,
                    output_type=ErJob,
                    endpoint=self.ctx.get_roll()._command_endpoint,
                    command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                    serdes_type=SerdesTypes.PROTOBUF)

            return result
        send_command()

        populated_store = self.ctx.populate_processor(self.__store)
        transfer_pair = TransferPair(transfer_id=job_id)
        done_cnt = 0
        for k, v in transfer_pair.gather(populated_store):
            done_cnt +=1
            yield self.key_serdes.deserialize(k), self.value_serdes.deserialize(v)
        LOGGER.debug(f"get_all count:{done_cnt}")

    def put_all(self, items, output=None, options={}):
        include_key = options.get("include_key", True)
        job_id = generate_job_id(self.__session_id, RollPair.PUT_ALL)

        # TODO:1: consider multiprocessing scenario. parallel size should be sent to egg_pair to set write signal count
        def send_command():
            job = ErJob(id=job_id,
                        name=RollPair.PUT_ALL,
                        inputs=[self.__store],
                        outputs=[self.__store],
                        functors=[])

            result = self.__command_client.simple_sync_send(
                    input=job,
                    output_type=ErJob,
                    endpoint=self.ctx.get_roll()._command_endpoint,
                    command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                    serdes_type=SerdesTypes.PROTOBUF)

            return result
        th = Thread(target=send_command)
        th.start()
        populated_store = self.ctx.populate_processor(self.__store)
        shuffler = TransferPair(job_id)
        broker = FifoBroker()
        bb = BatchBroker(broker)
        scatter_future = shuffler.scatter(broker, self.partitioner, populated_store)

        key_serdes = self.key_serdes
        value_serdes = self.value_serdes
        try:
            if include_key:
                for k, v in items:
                    bb.put(item=(key_serdes.serialize(k), value_serdes.serialize(v)))
            else:
                k = 0
                for v in items:
                    bb.put(item=(key_serdes.serialize(k), value_serdes.serialize(v)))
                    k += 1
        finally:
            bb.signal_write_finish()

        scatter_results = scatter_future.result()
        LOGGER.debug(f"scatter_results:{scatter_results}")
        th.join()
        return RollPair(populated_store, self.ctx)

    def count(self):
        total_partitions = self.__store._store_locator._total_partitions
        job_id = generate_job_id(self.__session_id, tag=RollPair.COUNT)
        job = ErJob(id=job_id,
                    name=RollPair.COUNT,
                    inputs=[self.ctx.populate_processor(self.__store)])
        args = list()
        for i in range(total_partitions):
            partition_input = job._inputs[0]._partitions[i]
            task = ErTask(id=generate_task_id(job_id, i),
                          name=job._name,
                          inputs=[partition_input],
                          job=job)
            args.append(([task], partition_input._processor._command_endpoint))

        futures = self.__command_client.async_call(
                args=args,
                output_types=[ErPair],
                command_uri=CommandURI(f'{RollPair.EGG_PAIR_URI_PREFIX}/{RollPair.RUN_TASK}'))

        done = wait(futures, timeout=10, return_when=FIRST_EXCEPTION).done

        result = 0
        for future in done:
            pair = future.result()[0]
            result += self.value_serdes.deserialize(pair._value)

        return result

    # todo:1: move to command channel to utilize batch command
    def destroy(self):
        total_partitions = self.__store._store_locator._total_partitions

        job = ErJob(id=generate_job_id(self.__session_id, RollPair.DESTROY),
                    name=RollPair.DESTROY,
                    inputs=[self.__store],
                    outputs=[],
                    functors=[])

        job_resp = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        LOGGER.info(f'{RollPair.DESTROY}: {self.__store}')

    def delete(self, k, options={}):
        key = create_serdes(self.__store._store_locator._serdes).serialize(k)
        er_pair = ErPair(key=key, value=None)
        outputs = []
        value = None
        partition_id = self.partitioner(key)
        egg = self.ctx.route_to_egg(self.__store._partitions[partition_id])
        LOGGER.info(egg._command_endpoint)
        LOGGER.info(f"count: {self.__store._store_locator._total_partitions}")
        inputs = [ErPartition(id=partition_id, store_locator=self.__store._store_locator)]
        output = [ErPartition(id=partition_id, store_locator=self.__store._store_locator)]

        job_id = generate_job_id(self.__session_id, RollPair.DELETE)
        job = ErJob(id=job_id,
                    name=RollPair.DELETE,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[ErFunctor(body=cloudpickle.dumps(er_pair))])
        task = ErTask(id=generate_task_id(job_id, partition_id), name=RollPair.DELETE, inputs=inputs, outputs=output, job=job)
        LOGGER.info("start send req")
        job_resp = self.__command_client.simple_sync_send(
                input=task,
                output_type=ErPair,
                endpoint=egg._command_endpoint,
                command_uri=CommandURI(f'{RollPair.EGG_PAIR_URI_PREFIX}/{RollPair.RUN_TASK}'),
                serdes_type=self.__command_serdes
        )
        LOGGER.info("get resp:{}".format(ErPair.from_proto_string(job_resp._value)))

    def take(self, n: int, options={}):
        if n <= 0:
            n = 1

        keys_only = options.get("keys_only", False)
        ret = []
        count = 0
        for item in self.get_all():
            if keys_only:
                if item :
                    ret.append(item[0])
                else:
                    ret.append(None)
            else:
                ret.append(item)
            count += 1
            if count == n:
                break
        return ret

    def first(self, options={}):
        resp = self.take(1, options=options)
        if resp:
            return resp[0]
        else:
            return None

    def save_as(self, name, namespace, partition, options={}):
        store_type = options.get('store_type', self.ctx.default_store_type)
        store = ErStore(store_locator=ErStoreLocator(store_type=store_type, namespace=namespace,
                                                     name=name, total_partitions=partition))
        return self.map_values(lambda v: v, output=store)

    # computing api
    def map_values(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.MAP_VALUES, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)
        # todo:1: options issues. refer to line 77
        final_options = {}
        final_options.update(self.__store._options)
        final_options.update(options)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.MAP_VALUES),
                    name=RollPair.MAP_VALUES,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor],
                    options=final_options)

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def map(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.MAP, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.MAP),
                    name=RollPair.MAP,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        er_store = job_result._outputs[0]
        LOGGER.info(er_store)
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def map_partitions(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.MAP_PARTITIONS, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.MAP_PARTITIONS),
                    name=RollPair.MAP_PARTITIONS,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes
        )
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def collapse_partitions(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.COLLAPSE_PARTITIONS, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)

        job = ErJob(id=generate_job_id(self.__session_id, RollPair.COLLAPSE_PARTITIONS),
                    name=RollPair.COLLAPSE_PARTITIONS,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes
        )
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def flat_map(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.FLAT_MAP, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)

        job = ErJob(id=generate_job_id(self.__session_id, RollPair.FLAT_MAP),
                    name=RollPair.FLAT_MAP,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes
        )
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def reduce(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.REDUCE, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))

        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.REDUCE),
                    name=RollPair.REDUCE,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        er_store = job_result._outputs[0]

        return RollPair(er_store, self.ctx)

    def aggregate(self, zero_value, seq_op, comb_op, output=None, options={}):
        zero_value_functor = ErFunctor(name=RollPair.AGGREGATE, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(zero_value))
        seq_op_functor = ErFunctor(name=RollPair.AGGREGATE, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(seq_op))
        comb_op_functor = ErFunctor(name=RollPair.AGGREGATE, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(comb_op))

        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.AGGREGATE),
                    name=RollPair.AGGREGATE,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[zero_value_functor, seq_op_functor, comb_op_functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        er_store = job_result._outputs[0]

        return RollPair(er_store, self.ctx)

    def glom(self, output=None, options={}):
        functor = ErFunctor(name=RollPair.GLOM, serdes=SerdesTypes.CLOUD_PICKLE)
        outputs = []
        if output:
            outputs.append(output)

        job = ErJob(id=generate_job_id(self.__session_id, RollPair.GLOM),
                    name=RollPair.GLOM,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes
        )
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def sample(self, fraction, seed=None, output=None, options={}):
        er_fraction = ErFunctor(name=RollPair.REDUCE, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(fraction))
        er_seed  = ErFunctor(name=RollPair.REDUCE, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(seed))

        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.SAMPLE),
                    name=RollPair.SAMPLE,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[er_fraction, er_seed])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def filter(self, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.FILTER, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))

        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.FILTER),
                    name=RollPair.FILTER,
                    inputs=[self.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)

        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def subtract_by_key(self, other, output=None, options={}):
        functor = ErFunctor(name=RollPair.SUBTRACT_BY_KEY, serdes=SerdesTypes.CLOUD_PICKLE)
        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.SUBTRACT_BY_KEY),
                    name=RollPair.SUBTRACT_BY_KEY,
                    inputs=[self.__store, other.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def union(self, other, func=lambda v1, v2: v1, output=None, options={}):
        functor = ErFunctor(name=RollPair.UNION, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.UNION),
                    name=RollPair.UNION,
                    inputs=[self.__store, other.__store],
                    outputs=outputs,
                    functors=[functor])

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)

    def join(self, other, func, output=None, options={}):
        functor = ErFunctor(name=RollPair.JOIN, serdes=SerdesTypes.CLOUD_PICKLE, body=cloudpickle.dumps(func))
        outputs = []
        if output:
            outputs.append(output)
        final_options = {}
        final_options.update(self.__store._options)
        final_options.update(options)
        job = ErJob(id=generate_job_id(self.__session_id, RollPair.JOIN),
                    name=RollPair.JOIN,
                    inputs=[self.__store, other.__store],
                    outputs=outputs,
                    functors=[functor],
                    options=final_options)

        job_result = self.__command_client.simple_sync_send(
                input=job,
                output_type=ErJob,
                endpoint=self.ctx.get_roll()._command_endpoint,
                command_uri=CommandURI(f'{RollPair.ROLL_PAIR_URI_PREFIX}/{RollPair.RUN_JOB}'),
                serdes_type=self.__command_serdes)
        er_store = job_result._outputs[0]
        LOGGER.info(er_store)

        return RollPair(er_store, self.ctx)
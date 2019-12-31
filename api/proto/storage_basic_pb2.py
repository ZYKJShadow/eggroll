# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: storage-basic.proto

import sys
_b=sys.version_info[0]<3 and (lambda x:x) or (lambda x:x.encode('latin1'))
from google.protobuf.internal import enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='storage-basic.proto',
  package='com.webank.ai.eggroll.api.storage',
  syntax='proto3',
  serialized_options=None,
  serialized_pb=_b('\n\x13storage-basic.proto\x12!com.webank.ai.eggroll.api.storage\"\x81\x01\n\x0eStorageLocator\x12<\n\x04type\x18\x01 \x01(\x0e\x32..com.webank.ai.eggroll.api.storage.StorageType\x12\x11\n\tnamespace\x18\x02 \x01(\t\x12\x0c\n\x04name\x18\x03 \x01(\t\x12\x10\n\x08\x66ragment\x18\x04 \x01(\x05*?\n\x0bStorageType\x12\x0c\n\x08LEVEL_DB\x10\x00\x12\r\n\tIN_MEMORY\x10\x01\x12\x08\n\x04LMDB\x10\x02\x12\t\n\x05REDIS\x10\x03\x62\x06proto3')
)

_STORAGETYPE = _descriptor.EnumDescriptor(
  name='StorageType',
  full_name='com.webank.ai.eggroll.api.storage.StorageType',
  filename=None,
  file=DESCRIPTOR,
  values=[
    _descriptor.EnumValueDescriptor(
      name='LEVEL_DB', index=0, number=0,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='IN_MEMORY', index=1, number=1,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='LMDB', index=2, number=2,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='REDIS', index=3, number=3,
      serialized_options=None,
      type=None),
  ],
  containing_type=None,
  serialized_options=None,
  serialized_start=190,
  serialized_end=253,
)
_sym_db.RegisterEnumDescriptor(_STORAGETYPE)

StorageType = enum_type_wrapper.EnumTypeWrapper(_STORAGETYPE)
LEVEL_DB = 0
IN_MEMORY = 1
LMDB = 2
REDIS = 3



_STORAGELOCATOR = _descriptor.Descriptor(
  name='StorageLocator',
  full_name='com.webank.ai.eggroll.api.storage.StorageLocator',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='type', full_name='com.webank.ai.eggroll.api.storage.StorageLocator.type', index=0,
      number=1, type=14, cpp_type=8, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='namespace', full_name='com.webank.ai.eggroll.api.storage.StorageLocator.namespace', index=1,
      number=2, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='name', full_name='com.webank.ai.eggroll.api.storage.StorageLocator.name', index=2,
      number=3, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='fragment', full_name='com.webank.ai.eggroll.api.storage.StorageLocator.fragment', index=3,
      number=4, type=5, cpp_type=1, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=59,
  serialized_end=188,
)

_STORAGELOCATOR.fields_by_name['type'].enum_type = _STORAGETYPE
DESCRIPTOR.message_types_by_name['StorageLocator'] = _STORAGELOCATOR
DESCRIPTOR.enum_types_by_name['StorageType'] = _STORAGETYPE
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

StorageLocator = _reflection.GeneratedProtocolMessageType('StorageLocator', (_message.Message,), dict(
  DESCRIPTOR = _STORAGELOCATOR,
  __module__ = 'storage_basic_pb2'
  # @@protoc_insertion_point(class_scope:com.webank.ai.eggroll.api.storage.StorageLocator)
  ))
_sym_db.RegisterMessage(StorageLocator)


# @@protoc_insertion_point(module_scope)
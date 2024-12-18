# -*- encoding: utf-8 -*-
'''
@Time    :   2024/12/17 15:38:21
@Author  :   Li Zeng 
'''


import numpy as np
import tensorrt as trt
import pycuda.driver as cuda
import pycuda.autoinit


# For ONNX:

class ONNXClassifierWrapper():
    def __init__(self, file, target_dtype=np.float32, num_classes=40):
        self.target_dtype = target_dtype
        self.num_classes = num_classes
        self.load(file)

        self.stream = None

    def load(self, file):
        with open(file, "rb") as f:
            self.runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
            self.engine = self.runtime.deserialize_cuda_engine(f.read())
            self.context = self.engine.create_execution_context()

    def allocate_memory(self, batch):
        print(batch.shape)
        input_shape = batch.shape  # Assuming the first dimension is the batch size
        batch_size = input_shape[0]

        output_shape = (batch_size, self.num_classes)

        self.output = np.empty(output_shape, dtype=self.target_dtype)  # Need to set both input and output precisions to FP16 to fully enable FP16

        # Allocate device memory
        self.d_input = cuda.mem_alloc(1 * batch.nbytes)
        self.d_output = cuda.mem_alloc(1 * self.output.nbytes)

        tensor_names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        assert (len(tensor_names) == 2)

        self.context.set_tensor_address(tensor_names[0], int(self.d_input))
        self.context.set_tensor_address(tensor_names[1], int(self.d_output))

        self.stream = cuda.Stream()

    def predict(self, batch):  # result gets copied into output
        if self.stream is None:
            self.allocate_memory(batch)

        # Transfer input data to device
        cuda.memcpy_htod_async(self.d_input, batch, self.stream)
        # Execute model
        self.context.execute_async_v3(self.stream.handle)
        # Transfer predictions back
        cuda.memcpy_dtoh_async(self.output, self.d_output, self.stream)
        # Syncronize threads
        self.stream.synchronize()

        return self.output


def convert_onnx_to_engine(onnx_filename, engine_filename=None, max_workspace_size=1 << 30, fp16_mode=True):
    logger = trt.Logger(trt.Logger.WARNING)
    with trt.Builder(logger) as builder, builder.create_network() as network, trt.OnnxParser(network,
                                                                                             logger) as parser, builder.create_builder_config() as builder_config:
        builder_config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, max_workspace_size)
        if (fp16_mode):
            builder_config.set_flag(trt.BuilderFlag.FP16)

        print("Parsing ONNX file.")
        with open(onnx_filename, 'rb') as model:
            if not parser.parse(model.read()):
                for error in range(parser.num_errors):
                    print(parser.get_error(error))

        print("Building TensorRT engine. This may take a few minutes.")
        serialized_engine = builder.build_serialized_network(network, builder_config)
        print("Building TensorRT engine end.")

        if engine_filename:
            with open(engine_filename, 'wb') as f:
                f.write(serialized_engine)

        return serialized_engine, logger
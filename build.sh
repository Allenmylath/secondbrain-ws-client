#!/bin/bash
# Install system dependencies
apt-get update
apt-get install -y portaudio19-dev libasound2-dev python3-dev python3-setuptools

# Install grpcio-tools for generating protobuf files
pip install grpcio-tools

# Generate protobuf files
python -m grpc_tools.protoc --proto_path=./ --python_out=./ frames.proto

echo "Build completed successfully"

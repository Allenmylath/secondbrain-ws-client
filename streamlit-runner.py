import streamlit as st
import websocket
import threading
import numpy as np
import time
import json
import pyaudio
import wave
import io
import os
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.message_factory import MessageFactory
from google.protobuf.descriptor_pool import DescriptorPool
import sounddevice as sd
import soundfile as sf

# Set page configuration
st.set_page_config(page_title="Pipecat WebSocket Client", layout="wide")

# Constants
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
CHUNK_SIZE = 512
FORMAT = pyaudio.paInt16
PLAY_TIME_RESET_THRESHOLD_MS = 1.0

# Create frames.proto file
frames_proto = """
//
// Copyright (c) 2024–2025, Daily
//
// SPDX-License-Identifier: BSD 2-Clause License
//
// Generate frames_pb2.py with:
//
//   python -m grpc_tools.protoc --proto_path=./ --python_out=./protobufs frames.proto
syntax = "proto3";
package pipecat;
message TextFrame {
  uint64 id = 1;
  string name = 2;
  string text = 3;
}
message AudioRawFrame {
  uint64 id = 1;
  string name = 2;
  bytes audio = 3;
  uint32 sample_rate = 4;
  uint32 num_channels = 5;
  optional uint64 pts = 6;
}
message TranscriptionFrame {
  uint64 id = 1;
  string name = 2;
  string text = 3;
  string user_id = 4;
  string timestamp = 5;
}
message Frame {
  oneof frame {
    TextFrame text = 1;
    AudioRawFrame audio = 2;
    TranscriptionFrame transcription = 3;
  }
}
"""

# Write the proto file to disk
with open("frames.proto", "w") as f:
    f.write(frames_proto)

# Initialize session state
if 'ws' not in st.session_state:
    st.session_state.ws = None
if 'is_playing' not in st.session_state:
    st.session_state.is_playing = False
if 'audio_queue' not in st.session_state:
    st.session_state.audio_queue = []
if 'status' not in st.session_state:
    st.session_state.status = "Ready to connect"
if 'play_time' not in st.session_state:
    st.session_state.play_time = 0
if 'last_message_time' not in st.session_state:
    st.session_state.last_message_time = 0

# Generate Python protobuf classes
try:
    import subprocess
    result = subprocess.run([
        "python", "-m", "grpc_tools.protoc", 
        "--proto_path=./", 
        "--python_out=./", 
        "frames.proto"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        st.error(f"Error generating protobuf classes: {result.stderr}")
    else:
        st.success("Generated protobuf classes successfully")
        
    # Import the generated module
    import frames_pb2
except Exception as e:
    st.error(f"Error setting up protobuf: {str(e)}")
    st.info("Trying alternative approach with dynamic loading...")
    
    # Alternative: Create descriptor pool and load message types dynamically
    from google.protobuf.compiler import plugin_pb2
    from google.protobuf import descriptor_pb2
    from google.protobuf.descriptor_pool import DescriptorPool
    from google.protobuf.message_factory import MessageFactory
    
    pool = DescriptorPool()
    with open("frames.proto", "rb") as f:
        proto_content = f.read()
        
    # This part would need protoc to be installed and called
    # For now, we'll assume frames_pb2 is generated

# Main app
st.title("Pipecat WebSocket Client")
st.subheader("Connect to a WebSocket server for voice communication")

# Server URL input
server_url = st.text_input("WebSocket Server URL", value="ws://localhost:8765")

# Status area
status_area = st.empty()
status_area.info(st.session_state.status)

# Convert Float32 audio to S16PCM
def convert_float32_to_s16pcm(float32_array):
    float32_array = np.clip(float32_array, -1.0, 1.0)
    int16_array = (float32_array * 32767).astype(np.int16)
    return int16_array.tobytes()

# WebSocket callback functions
def on_message(ws, message):
    try:
        # Parse the message using protobuf
        frame = frames_pb2.Frame()
        frame.ParseFromString(message)
        
        if frame.HasField("audio"):
            audio_data = np.frombuffer(frame.audio.audio, dtype=np.int16)
            
            # Calculate current time
            current_time = time.time()
            diff_time = current_time - st.session_state.last_message_time
            
            if st.session_state.play_time == 0 or diff_time > PLAY_TIME_RESET_THRESHOLD_MS:
                st.session_state.play_time = current_time
                
            st.session_state.last_message_time = current_time
            
            # Play audio
            if st.session_state.is_playing:
                # Convert to float32 for sounddevice
                float_data = audio_data.astype(np.float32) / 32768.0
                
                # Play audio in a separate thread to avoid blocking
                threading.Thread(
                    target=sd.play,
                    args=(float_data, SAMPLE_RATE),
                    daemon=True
                ).start()
                
                st.session_state.status = "Received audio data"
                status_area.info(st.session_state.status)
    except Exception as e:
        st.session_state.status = f"Error processing message: {str(e)}"
        status_area.error(st.session_state.status)

def on_error(ws, error):
    st.session_state.status = f"WebSocket error: {str(error)}"
    status_area.error(st.session_state.status)

def on_close(ws, close_status_code, close_msg):
    st.session_state.status = "WebSocket connection closed"
    status_area.warning(st.session_state.status)
    st.session_state.is_playing = False

def on_open(ws):
    st.session_state.status = "WebSocket connection established"
    status_area.success(st.session_state.status)
    
    # Start audio processing in a separate thread
    threading.Thread(target=process_audio, args=(ws,), daemon=True).start()

def process_audio(ws):
    try:
        # Initialize PyAudio
        p = pyaudio.PyAudio()
        
        # Open stream
        stream = p.open(
            format=FORMAT,
            channels=NUM_CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        
        st.session_state.status = "Audio stream started"
        status_area.info(st.session_state.status)
        
        # Process audio while the connection is active
        while st.session_state.is_playing and ws.sock and ws.sock.connected:
            try:
                # Read audio chunk
                audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                
                # Create protobuf message
                frame = frames_pb2.Frame()
                frame.audio.audio = audio_data
                frame.audio.sample_rate = SAMPLE_RATE
                frame.audio.num_channels = NUM_CHANNELS
                
                # Send the message
                ws.send(frame.SerializeToString())
                
            except Exception as e:
                st.session_state.status = f"Error processing audio: {str(e)}"
                status_area.error(st.session_state.status)
                break
        
        # Close stream
        stream.stop_stream()
        stream.close()
        p.terminate()
        
    except Exception as e:
        st.session_state.status = f"Error in audio processing: {str(e)}"
        status_area.error(st.session_state.status)

def start_websocket():
    try:
        # Close existing connection if any
        if st.session_state.ws:
            st.session_state.ws.close()
            
        # Create new WebSocket connection
        ws = websocket.WebSocketApp(
            server_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        st.session_state.ws = ws
        st.session_state.is_playing = True
        st.session_state.play_time = 0
        
        # Start WebSocket in a separate thread
        wst = threading.Thread(target=ws.run_forever)
        wst.daemon = True
        wst.start()
        
        st.session_state.status = "Connecting to WebSocket server..."
        status_area.info(st.session_state.status)
        
    except Exception as e:
        st.session_state.status = f"Error starting WebSocket: {str(e)}"
        status_area.error(st.session_state.status)

def stop_websocket():
    try:
        st.session_state.is_playing = False
        
        if st.session_state.ws:
            st.session_state.ws.close()
            st.session_state.ws = None
            
        st.session_state.status = "WebSocket connection stopped"
        status_area.warning(st.session_state.status)
        
    except Exception as e:
        st.session_state.status = f"Error stopping WebSocket: {str(e)}"
        status_area.error(st.session_state.status)

# UI controls
col1, col2 = st.columns(2)

with col1:
    start_button = st.button("Start Audio", 
                             on_click=start_websocket, 
                             disabled=st.session_state.is_playing)

with col2:
    stop_button = st.button("Stop Audio", 
                            on_click=stop_websocket, 
                            disabled=not st.session_state.is_playing)

# Installation instructions
st.markdown("---")
st.subheader("Installation Requirements")
st.code("""
# Install required packages
pip install streamlit websocket-client numpy pyaudio sounddevice soundfile protobuf grpcio-tools

# Run the app
streamlit run app.py
""")

# Usage information
st.markdown("---")
st.subheader("How it works")
st.markdown("""
1. Enter the WebSocket server URL (default: ws://localhost:8765)
2. Click "Start Audio" to establish a connection and begin streaming
3. The app will send microphone audio to the server
4. Any audio received from the server will be played back
5. Click "Stop Audio" to disconnect
""")

# Additional information
st.markdown("---")
st.subheader("About Protocol Buffers")
st.markdown("""
This app uses the Protocol Buffers (protobuf) format specified in `frames.proto` to encode and decode 
audio data. The protobuf definition includes message types for text, audio, and transcription data.
""")

# Display the proto definition
with st.expander("View frames.proto definition"):
    st.code(frames_proto, language="proto")

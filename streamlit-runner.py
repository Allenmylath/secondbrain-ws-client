import streamlit as st
import websocket
import numpy as np
import time
import pyaudio
import sounddevice as sd
import os
import asyncio
import concurrent.futures
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message_factory import MessageFactory

# Set page configuration
st.set_page_config(page_title="Pipecat WebSocket Client", layout="wide")

# Constants
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
CHUNK_SIZE = 512
FORMAT = pyaudio.paInt16
PLAY_TIME_RESET_THRESHOLD_MS = 1.0

try:
    import frames_pb2
    st.success("Successfully imported frames_pb2")
except ImportError:
    st.error("Could not import frames_pb2. Make sure the build process generated this file.")
    st.stop()

# Initialize session state
if 'ws' not in st.session_state:
    st.session_state.ws = None
if 'is_playing' not in st.session_state:
    st.session_state.is_playing = False
if 'audio_queue' not in st.session_state:
    st.session_state.audio_queue = []
if 'status' not in st.session_state:
    st.session_state.status = "Ready to connect"
if 'status_type' not in st.session_state:
    st.session_state.status_type = "info"  # Can be "info", "success", "warning", "error"
if 'play_time' not in st.session_state:
    st.session_state.play_time = 0
if 'last_message_time' not in st.session_state:
    st.session_state.last_message_time = 0
if 'executor' not in st.session_state:
    st.session_state.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
if 'loop' not in st.session_state:
    st.session_state.loop = None
if 'tasks' not in st.session_state:
    st.session_state.tasks = []

# Function to update status (use this instead of directly updating the UI)
def update_status(message, status_type="info"):
    st.session_state.status = message
    st.session_state.status_type = status_type

# Main app
st.title("Pipecat WebSocket Client")
st.header("Connect to a WebSocket server for voice communication")

# Server URL input
server_url = st.text_input("WebSocket Server URL", value="ws://localhost:8765")

# Status area - updates based on session state
status_area = st.empty()
if st.session_state.status_type == "info":
    status_area.info(st.session_state.status)
elif st.session_state.status_type == "success":
    status_area.success(st.session_state.status)
elif st.session_state.status_type == "warning":
    status_area.warning(st.session_state.status)
elif st.session_state.status_type == "error":
    status_area.error(st.session_state.status)

# Play audio using sounddevice in a ThreadPoolExecutor
def play_audio(audio_data):
    try:
        # Convert to float32 for sounddevice
        float_data = audio_data.astype(np.float32) / 32768.0
        sd.play(float_data, SAMPLE_RATE)
        sd.wait()  # Wait until audio is done playing
    except Exception as e:
        print(f"Error playing audio: {str(e)}")

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
            
            # Play audio in the thread pool
            if st.session_state.is_playing:
                st.session_state.executor.submit(play_audio, audio_data)
                update_status("Received audio data", "info")
    except Exception as e:
        update_status(f"Error processing message: {str(e)}", "error")

def on_error(ws, error):
    update_status(f"WebSocket error: {str(error)}", "error")

def on_close(ws, close_status_code, close_msg):
    update_status("WebSocket connection closed", "warning")
    st.session_state.is_playing = False

def on_open(ws):
    update_status("WebSocket connection established", "success")
    
    # Start audio processing in the executor
    future = st.session_state.executor.submit(process_audio, ws)
    st.session_state.tasks.append(future)

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
        
        update_status("Audio stream started", "info")
        
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
                update_status(f"Error processing audio: {str(e)}", "error")
                break
        
        # Close stream
        stream.stop_stream()
        stream.close()
        p.terminate()
        
    except Exception as e:
        update_status(f"Error in audio processing: {str(e)}", "error")

# Async function to run WebSocket
async def run_websocket(ws):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, ws.run_forever)

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
        
        # Enable heartbeats and reconnection
        ws.enable_multithread = True
        
        # Use ThreadPoolExecutor to run the WebSocket
        future = st.session_state.executor.submit(
            ws.run_forever,
            ping_interval=30,  # Send ping every 30 seconds
            ping_timeout=10,   # Wait 10 seconds for pong
            reconnect=5        # Try to reconnect 5 times
        )
        st.session_state.tasks.append(future)
        
        update_status("Connecting to WebSocket server...", "info")
        
    except Exception as e:
        update_status(f"Error starting WebSocket: {str(e)}", "error")

def stop_websocket():
    try:
        st.session_state.is_playing = False
        
        if st.session_state.ws:
            st.session_state.ws.close()
            st.session_state.ws = None
        
        # Cancel any pending tasks
        for task in st.session_state.tasks:
            if not task.done():
                try:
                    task.cancel()
                except Exception:
                    pass
        st.session_state.tasks = []
            
        update_status("WebSocket connection stopped", "warning")
        
    except Exception as e:
        update_status(f"Error stopping WebSocket: {str(e)}", "error")

# Cleanup function for session shutdown
def cleanup():
    if hasattr(st.session_state, 'executor'):
        st.session_state.executor.shutdown(wait=False)

# Register cleanup handler
import atexit
atexit.register(cleanup)

# Add heartbeat display
last_received = st.empty()
if st.session_state.last_message_time > 0:
    last_received.text(f"Last audio received: {time.strftime('%H:%M:%S', time.localtime(st.session_state.last_message_time))}")

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

import streamlit as st
import websocket
import numpy as np
import time
import pyaudio
import sounddevice as sd
import concurrent.futures
import logging
import socket
import atexit

# Configure basic logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("websocket_client")

# Set page configuration
st.set_page_config(page_title="WebSocket Audio Client", layout="wide")

# Constants
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
CHUNK_SIZE = 512
FORMAT = pyaudio.paInt16

# Initialize session state
if 'ws' not in st.session_state:
    st.session_state.ws = None
if 'is_playing' not in st.session_state:
    st.session_state.is_playing = False
if 'status' not in st.session_state:
    st.session_state.status = "Ready to connect"
if 'status_type' not in st.session_state:
    st.session_state.status_type = "info"
if 'executor' not in st.session_state:
    st.session_state.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'audio_packets_sent' not in st.session_state:
    st.session_state.audio_packets_sent = 0
if 'audio_packets_received' not in st.session_state:
    st.session_state.audio_packets_received = 0

# Function to update status
def update_status(message, status_type="info"):
    st.session_state.status = message
    st.session_state.status_type = status_type

# Main app
st.title("WebSocket Audio Client")
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
        update_status(f"Error playing audio: {str(e)}", "error")

# WebSocket callback functions
def on_message(ws, message):
    try:
        # First 8 bytes: sample rate (4 bytes) and num channels (4 bytes)
        sample_rate = int.from_bytes(message[:4], byteorder='little')
        num_channels = int.from_bytes(message[4:8], byteorder='little')
        
        # Rest is audio data
        audio_data = np.frombuffer(message[8:], dtype=np.int16)
        
        st.session_state.audio_packets_received += 1
        
        # Play audio in the thread pool
        if st.session_state.is_playing:
            st.session_state.executor.submit(play_audio, audio_data)
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
                
                # Create binary message with header
                # First 8 bytes: sample rate (4 bytes) and num channels (4 bytes)
                header = SAMPLE_RATE.to_bytes(4, byteorder='little') + NUM_CHANNELS.to_bytes(4, byteorder='little')
                
                # Send header + audio data
                ws.send(header + audio_data)
                st.session_state.audio_packets_sent += 1
                
            except Exception as e:
                update_status(f"Error processing audio: {str(e)}", "error")
                break
        
        # Close stream
        stream.stop_stream()
        stream.close()
        p.terminate()
        
    except Exception as e:
        update_status(f"Error in audio processing: {str(e)}", "error")

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
        st.session_state.audio_packets_sent = 0
        st.session_state.audio_packets_received = 0
        
        # Enable multithread mode
        ws.enable_multithread = True
        
        # Configure socket for better stability
        def on_create_connection(wcapp):
            wcapp.sock.settimeout(30.0)
            if hasattr(socket, 'SO_KEEPALIVE'):
                wcapp.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        
        ws.on_create_connection = on_create_connection
        
        # Use ThreadPoolExecutor to run the WebSocket
        future = st.session_state.executor.submit(
            ws.run_forever,
            ping_interval=15,  # Send pings every 15 seconds
            ping_timeout=10,   # Wait 10 seconds for pong
            reconnect=5,       # Try to reconnect 5 times
            skip_utf8_validation=True  # Skip UTF-8 validation for better performance
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
atexit.register(cleanup)

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

# Display counters and stats
st.subheader("Statistics")
stats_col1, stats_col2 = st.columns(2)
with stats_col1:
    st.metric("Audio Packets Sent", st.session_state.audio_packets_sent)
with stats_col2:
    st.metric("Audio Packets Received", st.session_state.audio_packets_received)

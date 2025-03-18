import streamlit as st
import websocket
import numpy as np
import time
import pyaudio
import sounddevice as sd
import os
import asyncio
import concurrent.futures
import logging
from datetime import datetime
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message_factory import MessageFactory

# Configure logging
logging.basicConfig(level=logging.DEBUG, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("pipecat_websocket")

# Set page configuration
st.set_page_config(page_title="Pipecat WebSocket Client", layout="wide")

logger.debug("App started - Setting up page configuration")

# Constants
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
CHUNK_SIZE = 512
FORMAT = pyaudio.paInt16
PLAY_TIME_RESET_THRESHOLD_MS = 1.0

logger.debug(f"Constants initialized: SAMPLE_RATE={SAMPLE_RATE}, CHANNELS={NUM_CHANNELS}, CHUNK={CHUNK_SIZE}")

# Debug function for session state
def log_session_state(message=""):
    """Log the current state of all session variables"""
    logger.debug(f"--- SESSION STATE {message} ---")
    for key, value in st.session_state.items():
        if key == 'ws':
            state = "Connected" if value and hasattr(value, 'sock') and value.sock else "Disconnected"
            logger.debug(f"  {key}: {state}")
        elif key == 'audio_queue':
            logger.debug(f"  {key}: {len(value)} items in queue")
        elif key == 'executor' or key == 'loop':
            logger.debug(f"  {key}: {type(value)}")
        elif key == 'tasks':
            tasks_status = [f"Done: {t.done()}" for t in value]
            logger.debug(f"  {key}: {len(value)} tasks - {tasks_status}")
        else:
            logger.debug(f"  {key}: {value}")
    logger.debug(f"---------------------------")

# Show debug info in app
debug_expander = st.expander("Debug Information", expanded=False)

try:
    import frames_pb2
    logger.debug("Successfully imported frames_pb2")
    st.success("Successfully imported frames_pb2")
except ImportError:
    logger.error("Could not import frames_pb2. Make sure the build process generated this file.")
    st.error("Could not import frames_pb2. Make sure the build process generated this file.")
    st.stop()

logger.debug("Initializing session state variables")

# Initialize session state
if 'ws' not in st.session_state:
    logger.debug("Initializing ws session state to None")
    st.session_state.ws = None
if 'is_playing' not in st.session_state:
    logger.debug("Initializing is_playing session state to False")
    st.session_state.is_playing = False
if 'audio_queue' not in st.session_state:
    logger.debug("Initializing audio_queue session state to empty list")
    st.session_state.audio_queue = []
if 'status' not in st.session_state:
    logger.debug("Initializing status session state to 'Ready to connect'")
    st.session_state.status = "Ready to connect"
if 'status_type' not in st.session_state:
    logger.debug("Initializing status_type session state to 'info'")
    st.session_state.status_type = "info"  # Can be "info", "success", "warning", "error"
if 'play_time' not in st.session_state:
    logger.debug("Initializing play_time session state to 0")
    st.session_state.play_time = 0
if 'last_message_time' not in st.session_state:
    logger.debug("Initializing last_message_time session state to 0")
    st.session_state.last_message_time = 0
if 'executor' not in st.session_state:
    logger.debug("Initializing ThreadPoolExecutor with 3 workers")
    st.session_state.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
if 'loop' not in st.session_state:
    logger.debug("Initializing loop session state to None")
    st.session_state.loop = None
if 'tasks' not in st.session_state:
    logger.debug("Initializing tasks session state to empty list")
    st.session_state.tasks = []
if 'debug_messages' not in st.session_state:
    logger.debug("Initializing debug_messages session state to empty list")
    st.session_state.debug_messages = []
if 'audio_packets_sent' not in st.session_state:
    logger.debug("Initializing audio_packets_sent counter to 0")
    st.session_state.audio_packets_sent = 0
if 'audio_packets_received' not in st.session_state:
    logger.debug("Initializing audio_packets_received counter to 0")
    st.session_state.audio_packets_received = 0

log_session_state("after initialization")

# Function to add debug message
def add_debug_message(message):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    st.session_state.debug_messages.append(f"{timestamp}: {message}")
    # Keep only the last 100 messages
    if len(st.session_state.debug_messages) > 100:
        st.session_state.debug_messages = st.session_state.debug_messages[-100:]
    logger.debug(message)

# Function to update status (use this instead of directly updating the UI)
def update_status(message, status_type="info"):
    logger.debug(f"Status update: {status_type} - {message}")
    add_debug_message(f"Status: {status_type} - {message}")
    st.session_state.status = message
    st.session_state.status_type = status_type

# Main app
st.title("Pipecat WebSocket Client")
st.header("Connect to a WebSocket server for voice communication")

# Server URL input
server_url = st.text_input("WebSocket Server URL", value="ws://localhost:8765")
add_debug_message(f"Server URL set to: {server_url}")

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
        add_debug_message(f"Playing audio chunk: {len(audio_data)} samples")
        # Convert to float32 for sounddevice
        float_data = audio_data.astype(np.float32) / 32768.0
        sd.play(float_data, SAMPLE_RATE)
        sd.wait()  # Wait until audio is done playing
        add_debug_message(f"Finished playing audio")
    except Exception as e:
        error_msg = f"Error playing audio: {str(e)}"
        logger.error(error_msg, exc_info=True)
        add_debug_message(error_msg)

# WebSocket callback functions
def on_message(ws, message):
    try:
        # Parse the message using protobuf
        add_debug_message(f"Message received: {len(message)} bytes")
        frame = frames_pb2.Frame()
        frame.ParseFromString(message)
        
        if frame.HasField("audio"):
            audio_data = np.frombuffer(frame.audio.audio, dtype=np.int16)
            add_debug_message(f"Audio data received: {len(audio_data)} samples, {frame.audio.sample_rate}Hz, {frame.audio.num_channels} channels")
            
            # Calculate current time
            current_time = time.time()
            diff_time = current_time - st.session_state.last_message_time
            
            add_debug_message(f"Time since last message: {diff_time:.3f}s")
            
            if st.session_state.play_time == 0 or diff_time > PLAY_TIME_RESET_THRESHOLD_MS:
                add_debug_message(f"Resetting play time (old: {st.session_state.play_time})")
                st.session_state.play_time = current_time
                
            st.session_state.last_message_time = current_time
            st.session_state.audio_packets_received += 1
            
            # Play audio in the thread pool
            if st.session_state.is_playing:
                add_debug_message(f"Submitting audio for playback (packet #{st.session_state.audio_packets_received})")
                st.session_state.executor.submit(play_audio, audio_data)
                update_status(f"Received audio data (packet #{st.session_state.audio_packets_received})", "info")
        else:
            add_debug_message(f"Non-audio frame received: {frame}")
    except Exception as e:
        error_msg = f"Error processing message: {str(e)}"
        logger.error(error_msg, exc_info=True)
        add_debug_message(error_msg)
        update_status(error_msg, "error")

def on_error(ws, error):
    error_msg = f"WebSocket error: {str(error)}"
    logger.error(error_msg)
    add_debug_message(error_msg)
    update_status(error_msg, "error")

def on_close(ws, close_status_code, close_msg):
    msg = f"WebSocket connection closed: code={close_status_code}, message={close_msg}"
    logger.info(msg)
    add_debug_message(msg)
    update_status("WebSocket connection closed", "warning")
    st.session_state.is_playing = False
    log_session_state("after connection closed")

def on_open(ws):
    logger.info("WebSocket connection established")
    add_debug_message("WebSocket connection established")
    update_status("WebSocket connection established", "success")
    
    # Start audio processing in the executor
    add_debug_message("Submitting audio processing task to executor")
    future = st.session_state.executor.submit(process_audio, ws)
    st.session_state.tasks.append(future)
    log_session_state("after connection opened")

def process_audio(ws):
    try:
        add_debug_message("Starting audio processing")
        # Initialize PyAudio
        p = pyaudio.PyAudio()
        add_debug_message("PyAudio initialized")
        
        # Get device info
        info = p.get_host_api_info_by_index(0)
        numdevices = info.get('deviceCount')
        add_debug_message(f"Found {numdevices} audio devices")
        
        for i in range(0, numdevices):
            device_info = p.get_device_info_by_host_api_device_index(0, i)
            add_debug_message(f"Device {i}: {device_info['name']}, inputs: {device_info['maxInputChannels']}")
        
        # Open stream
        add_debug_message(f"Opening audio stream: {SAMPLE_RATE}Hz, {NUM_CHANNELS} channels, {CHUNK_SIZE} chunk size")
        stream = p.open(
            format=FORMAT,
            channels=NUM_CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        
        add_debug_message("Audio stream started")
        update_status("Audio stream started", "info")
        
        # Process audio while the connection is active
        while st.session_state.is_playing and ws.sock and ws.sock.connected:
            try:
                # Read audio chunk
                add_debug_message(f"Reading audio chunk of size {CHUNK_SIZE}")
                audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                
                # Create protobuf message
                frame = frames_pb2.Frame()
                frame.audio.audio = audio_data
                frame.audio.sample_rate = SAMPLE_RATE
                frame.audio.num_channels = NUM_CHANNELS
                
                # Send the message
                add_debug_message(f"Sending audio frame: {len(audio_data)} bytes")
                ws.send(frame.SerializeToString())
                st.session_state.audio_packets_sent += 1
                add_debug_message(f"Audio frame sent (packet #{st.session_state.audio_packets_sent})")
                
            except Exception as e:
                error_msg = f"Error processing audio: {str(e)}"
                logger.error(error_msg, exc_info=True)
                add_debug_message(error_msg)
                update_status(error_msg, "error")
                break
        
        # Close stream
        add_debug_message("Stopping audio stream")
        stream.stop_stream()
        stream.close()
        p.terminate()
        add_debug_message("Audio stream closed and PyAudio terminated")
        
    except Exception as e:
        error_msg = f"Error in audio processing: {str(e)}"
        logger.error(error_msg, exc_info=True)
        add_debug_message(error_msg)
        update_status(error_msg, "error")

# Async function to run WebSocket
async def run_websocket(ws):
    logger.debug("Starting async WebSocket runner")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, ws.run_forever)
    logger.debug("Async WebSocket runner completed")

def start_websocket():
    try:
        add_debug_message(f"Starting WebSocket connection to {server_url}")
        
        # Close existing connection if any
        if st.session_state.ws:
            add_debug_message("Closing existing WebSocket connection")
            st.session_state.ws.close()
            
        # Create new WebSocket connection
        add_debug_message("Creating new WebSocket connection")
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
        st.session_state.audio_packets_sent = 0
        st.session_state.audio_packets_received = 0
        
        # Enable heartbeats and reconnection
        add_debug_message("Enabling WebSocket multithread mode")
        ws.enable_multithread = True
        
        # Use ThreadPoolExecutor to run the WebSocket
        add_debug_message("Submitting WebSocket run_forever to executor")
        future = st.session_state.executor.submit(
            ws.run_forever,
            ping_interval=30,  # Send ping every 30 seconds
            ping_timeout=10,   # Wait 10 seconds for pong
            reconnect=5        # Try to reconnect 5 times
        )
        st.session_state.tasks.append(future)
        
        update_status("Connecting to WebSocket server...", "info")
        log_session_state("after starting WebSocket")
        
    except Exception as e:
        error_msg = f"Error starting WebSocket: {str(e)}"
        logger.error(error_msg, exc_info=True)
        add_debug_message(error_msg)
        update_status(error_msg, "error")

def stop_websocket():
    try:
        add_debug_message("Stopping WebSocket connection")
        st.session_state.is_playing = False
        
        if st.session_state.ws:
            add_debug_message("Closing WebSocket connection")
            st.session_state.ws.close()
            st.session_state.ws = None
        
        # Cancel any pending tasks
        add_debug_message(f"Cancelling {len(st.session_state.tasks)} pending tasks")
        for task in st.session_state.tasks:
            if not task.done():
                try:
                    add_debug_message(f"Attempting to cancel task: {task}")
                    task.cancel()
                except Exception as e:
                    add_debug_message(f"Error cancelling task: {e}")
        st.session_state.tasks = []
            
        update_status("WebSocket connection stopped", "warning")
        log_session_state("after stopping WebSocket")
        
    except Exception as e:
        error_msg = f"Error stopping WebSocket: {str(e)}"
        logger.error(error_msg, exc_info=True)
        add_debug_message(error_msg)
        update_status(error_msg, "error")

# Cleanup function for session shutdown
def cleanup():
    logger.debug("Running cleanup function")
    if hasattr(st.session_state, 'executor'):
        logger.debug("Shutting down executor")
        add_debug_message("Shutting down executor")
        st.session_state.executor.shutdown(wait=False)

# Register cleanup handler
import atexit
atexit.register(cleanup)
logger.debug("Registered cleanup handler")

# Add heartbeat display
last_received = st.empty()
if st.session_state.last_message_time > 0:
    last_time_str = time.strftime('%H:%M:%S', time.localtime(st.session_state.last_message_time))
    last_received.text(f"Last audio received: {last_time_str}")
    add_debug_message(f"Updated last received time to {last_time_str}")

# UI controls
col1, col2 = st.columns(2)

with col1:
    start_button = st.button("Start Audio", 
                             on_click=start_websocket, 
                             disabled=st.session_state.is_playing)
    if st.session_state.is_playing:
        add_debug_message("Start Audio button disabled (already playing)")
    else:
        add_debug_message("Start Audio button enabled")

with col2:
    stop_button = st.button("Stop Audio", 
                            on_click=stop_websocket, 
                            disabled=not st.session_state.is_playing)
    if not st.session_state.is_playing:
        add_debug_message("Stop Audio button disabled (not playing)")
    else:
        add_debug_message("Stop Audio button enabled")

# Display counters and stats
st.subheader("Statistics")
stats_col1, stats_col2 = st.columns(2)
with stats_col1:
    st.metric("Audio Packets Sent", st.session_state.audio_packets_sent)
with stats_col2:
    st.metric("Audio Packets Received", st.session_state.audio_packets_received)

# Update debug info in the expander
with debug_expander:
    if st.button("Refresh Debug Info"):
        log_session_state("manual refresh")
    
    st.subheader("Session State")
    for key, value in st.session_state.items():
        if key == 'ws':
            state = "Connected" if value and hasattr(value, 'sock') and value.sock else "Disconnected"
            st.text(f"{key}: {state}")
        elif key == 'audio_queue':
            st.text(f"{key}: {len(value)} items in queue")
        elif key == 'executor' or key == 'loop':
            st.text(f"{key}: {type(value)}")
        elif key == 'tasks':
            tasks_status = [f"Done: {t.done()}" for t in value]
            st.text(f"{key}: {len(value)} tasks - {tasks_status}")
        elif key == 'debug_messages':
            continue  # Skip, we'll show these separately
        else:
            st.text(f"{key}: {value}")
    
    st.subheader("Debug Log")
    for msg in reversed(st.session_state.debug_messages):
        st.text(msg)

# Log the final state after rendering
log_session_state("after rendering UI")

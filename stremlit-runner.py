import streamlit as st
import websocket
import threading
import pyaudio
import numpy as np
import time
import base64
from io import BytesIO
import sounddevice as sd
from protobuf3.message import Message
from protobuf3.fields import BytesField, UInt32Field

# Set page configuration
st.set_page_config(
    page_title="Pipecat WebSocket Client",
    page_icon="🎤",
    layout="centered"
)

# Define constants
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
CHUNK_SIZE = 512
FORMAT = pyaudio.paInt16

# Simple ProtoBuffer implementation for Frame message
class AudioData(Message):
    audio = BytesField(field_number=1)
    sample_rate = UInt32Field(field_number=2)
    num_channels = UInt32Field(field_number=3)

class Frame(Message):
    audio = AudioData(field_number=1)

# State management
if 'ws_connection' not in st.session_state:
    st.session_state.ws_connection = None
if 'is_recording' not in st.session_state:
    st.session_state.is_recording = False
if 'received_audio' not in st.session_state:
    st.session_state.received_audio = []
if 'audio_thread' not in st.session_state:
    st.session_state.audio_thread = None

# Function to convert float32 audio to int16
def float32_to_int16(float32_array):
    float32_array = np.clip(float32_array, -1.0, 1.0)
    return (float32_array * 32767).astype(np.int16)

# Function to handle WebSocket messages
def on_message(ws, message):
    try:
        frame = Frame()
        frame.parse_from_bytes(message)
        
        if frame.audio and frame.audio.audio:
            audio_data = np.frombuffer(frame.audio.audio, dtype=np.int16)
            st.session_state.received_audio.append({
                'data': audio_data,
                'sample_rate': frame.audio.sample_rate
            })
            
            # Play the audio immediately
            sd.play(audio_data.astype(np.float32) / 32767.0, frame.audio.sample_rate)
    except Exception as e:
        st.error(f"Error processing received audio: {e}")

# Function to handle WebSocket errors
def on_error(ws, error):
    st.error(f"WebSocket error: {error}")
    stop_recording()

# Function to handle WebSocket close
def on_close(ws, close_status_code, close_msg):
    st.warning(f"WebSocket connection closed: {close_status_code} - {close_msg}")
    stop_recording()

# Function to handle WebSocket open
def on_open(ws):
    st.success("WebSocket connection established.")

# Function to start audio recording and transmission
def start_recording():
    def audio_callback(indata, frames, time, status):
        if status:
            st.error(f"Audio input error: {status}")
            return
        
        if st.session_state.ws_connection and st.session_state.ws_connection.sock and st.session_state.ws_connection.sock.connected:
            try:
                # Convert audio to int16
                audio_data = float32_to_int16(indata.flatten())
                
                # Create the protobuf message
                audio_msg = AudioData()
                audio_msg.audio = audio_data.tobytes()
                audio_msg.sample_rate = SAMPLE_RATE
                audio_msg.num_channels = NUM_CHANNELS
                
                frame = Frame()
                frame.audio = audio_msg
                
                # Send the encoded message
                st.session_state.ws_connection.send(frame.encode_to_bytes())
            except Exception as e:
                st.error(f"Error sending audio: {e}")
    
    # Start the audio recording in a separate thread
    def audio_thread_func():
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=NUM_CHANNELS, 
                           callback=audio_callback, blocksize=CHUNK_SIZE):
            while st.session_state.is_recording:
                time.sleep(0.1)
    
    st.session_state.audio_thread = threading.Thread(target=audio_thread_func)
    st.session_state.audio_thread.start()
    st.session_state.is_recording = True

# Function to stop recording
def stop_recording():
    st.session_state.is_recording = False
    if st.session_state.audio_thread:
        if st.session_state.audio_thread.is_alive():
            st.session_state.audio_thread.join(timeout=1.0)
        st.session_state.audio_thread = None
    
    if st.session_state.ws_connection:
        st.session_state.ws_connection.close()
        st.session_state.ws_connection = None

# Function to connect to WebSocket
def connect_websocket(server_url):
    if st.session_state.ws_connection:
        st.session_state.ws_connection.close()
    
    websocket.enableTrace(True)
    ws = websocket.WebSocketApp(
        server_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    # Start the WebSocket in a background thread
    def run_ws():
        ws.run_forever()
    
    thread = threading.Thread(target=run_ws)
    thread.daemon = True
    thread.start()
    
    st.session_state.ws_connection = ws
    return ws

# Function to get audio data for playback
def get_audio_playback_html(audio_data, sample_rate):
    audio_bytes = audio_data.tobytes()
    audio_base64 = base64.b64encode(audio_bytes).decode()
    return f"""
    <audio controls autoplay>
      <source src="data:audio/wav;base64,{audio_base64}" type="audio/wav">
      Your browser does not support the audio element.
    </audio>
    """

# Streamlit UI
st.title("🎤 Pipecat WebSocket Client")

# Server connection settings
with st.expander("Server Settings", expanded=True):
    col1, col2 = st.columns([3, 1])
    with col1:
        server_url = st.text_input("WebSocket Server URL", "ws://localhost:8765")
    with col2:
        connect_button = st.button("Connect")
        
        if connect_button:
            with st.spinner("Connecting to WebSocket server..."):
                connect_websocket(server_url)

# Audio control buttons
st.subheader("Audio Controls")
col1, col2 = st.columns(2)

with col1:
    start_button = st.button("Start Audio", type="primary", disabled=not st.session_state.ws_connection or st.session_state.is_recording)
    if start_button:
        start_recording()

with col2:
    stop_button = st.button("Stop Audio", type="secondary", disabled=not st.session_state.is_recording)
    if stop_button:
        stop_recording()

# Connection status indicator
connection_status = "Connected" if st.session_state.ws_connection and hasattr(st.session_state.ws_connection, 'sock') and st.session_state.ws_connection.sock and st.session_state.ws_connection.sock.connected else "Disconnected"
status_color = "green" if connection_status == "Connected" else "red"

st.markdown(f"""
<div style="display: flex; align-items: center; margin-top: 1em;">
    <div style="width: 10px; height: 10px; border-radius: 50%; background-color: {status_color}; margin-right: 5px;"></div>
    <span>Status: <b>{connection_status}</b></span>
</div>
""", unsafe_allow_html=True)

# Recent received audio
if st.session_state.received_audio:
    st.subheader("Recent Received Audio")
    for idx, audio in enumerate(st.session_state.received_audio[-5:]):
        with st.container():
            st.audio(audio['data'].astype(np.float32) / 32767.0, sample_rate=audio['sample_rate'])

# Instructions
with st.expander("Instructions"):
    st.markdown("""
    ## How to use this application
    
    1. Enter the WebSocket server URL (default is `ws://localhost:8765`)
    2. Click **Connect** to establish a connection with the server
    3. Click **Start Audio** to begin sending microphone audio to the server
    4. The server will process your audio and send responses back
    5. Click **Stop Audio** to end the session
    
    ## Requirements
    
    - A running Pipecat WebSocket server at the specified URL
    - Microphone access allowed in your browser
    - The server should follow the same protocol as defined in the original application
    """)

# Footer
st.markdown("""
---
Made with Streamlit | Pipecat WebSocket Client
""")

# Callback to ensure WebSocket is closed when the app is refreshed
def on_session_end():
    stop_recording()

# Register the callback
st.session_state.on_session_end = on_session_end

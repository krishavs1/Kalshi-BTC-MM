import json
import time
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# --- CONFIGURATION ---
# PASTE YOUR KEY ID INSIDE THE QUOTES BELOW
KEY_ID = "741711fe-2564-430e-b44b-e0aa8cd1b035" 

PRIVATE_KEY_PATH = "kalshi-key.pem"

def get_auth_headers(method="GET", path="/trade-api/ws/v2"):
    """
    Generates the signed headers required by Kalshi.
    For WebSockets, we sign the handshake request: GET /trade-api/ws/v2
    """
    # 1. Load Private Key
    try:
        with open(PRIVATE_KEY_PATH, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None
            )
    except FileNotFoundError:
        print(f"❌ ERROR: Could not find {PRIVATE_KEY_PATH}")
        print("Did you create the file and paste your key inside?")
        raise

    # 2. Prepare the Message to Sign
    # Kalshi requires: timestamp + method + path_no_query_params
    timestamp_ms = str(int(time.time() * 1000))
    msg_string = timestamp_ms + method + path
    
    # 3. Sign it
    signature = private_key.sign(
        msg_string.encode('utf-8'),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    
    sig_b64 = base64.b64encode(signature).decode('utf-8')

    # NEW HEADERS (Note the word 'ACCESS' instead of 'API')
    return {
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "Content-Type": "application/json"
    }
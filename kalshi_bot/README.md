# Kalshi Bitcoin Trading Bot - Phase 1: The Eyes

This bot connects to Kalshi's API and streams live Bitcoin market prices.

## Setup Instructions

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Generate RSA Keys
```bash
python keygen.py
```

This will create:
- `kalshi-key.pem` - Your private key (KEEP SECRET)
- `kalshi-public.pem` - Your public key (upload to Kalshi)

### Step 3: Upload Public Key to Kalshi
1. Open `kalshi-public.pem` and copy all the text
2. Go to Kalshi: Log in -> Settings -> API Keys -> "Add Key"
3. Paste your public key text
4. Copy the Key ID (UUID) that Kalshi gives you

### Step 4: Configure Authentication
Edit `auth.py` and replace `YOUR_KEY_ID_HERE` with the Key ID from Step 3.

### Step 5: Test Market Selection
```bash
python selector.py
```

This will show you which markets are available.

### Step 6: Start the Listener
```bash
python listener.py
```

You should see live price updates streaming in your terminal!

## Files

- `keygen.py` - Generates RSA key pair for authentication
- `auth.py` - Handles authentication headers (update with your Key ID)
- `selector.py` - Finds Bitcoin markets for the next hour
- `listener.py` - Connects to WebSocket and streams live prices
- `requirements.txt` - Python dependencies


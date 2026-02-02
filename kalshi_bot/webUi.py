"""Web-based UI for profit monitoring"""
from flask import Flask, render_template, jsonify
import threading
import time
from datetime import datetime

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Shared data structure (thread-safe with locks)
data_lock = threading.Lock()
latest_data = {
    'sets': [],  # List of dicts, each with 'orderbooks', 'profits', 'tickers', 'ready_for_trading'
    'last_update': None
}

def update_data(all_sets_data):
    """Update the shared data structure
    
    Args:
        all_sets_data: List of dicts, each with 'orderbooks', 'profits', 'tickers' for one set
    """
    global latest_data
    with data_lock:
        latest_data = {
            'sets': all_sets_data,
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('profit_monitor.html')

@app.route('/api/data')
def get_data():
    """API endpoint to get latest data"""
    with data_lock:
        return jsonify(latest_data)

def run_server(host='127.0.0.1', port=5000, debug=False):
    """Run the Flask server"""
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


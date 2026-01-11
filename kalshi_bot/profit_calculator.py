"""Profit calculation and CSV logging functions"""
import csv
import os
from datetime import datetime

def calculate_profits(range_ob, lower_leg_ob, higher_leg_ob):
    """Calculate 2 potential profit values for limit order strategy"""
    if not (range_ob and lower_leg_ob and higher_leg_ob):
        return None
    
    # Extract values
    range_yes_ask = range_ob['yes_ask']
    range_no_ask = range_ob['no_ask']
    
    lower_yes_ask = lower_leg_ob['yes_ask']
    lower_no_ask = lower_leg_ob['no_ask']
    
    higher_yes_ask = higher_leg_ob['yes_ask']
    higher_no_ask = higher_leg_ob['no_ask']
    
    # Profit 1: (Ask of Range YES − 1) − (Ask of Lower Leg YES) − (Ask of Higher Leg NO) + 100
    profit1 = (range_yes_ask - 1) - lower_yes_ask - higher_no_ask + 100
    
    # Profit 2: (Ask of Range NO − 1) − Ask of lower leg NO − Ask of higher leg YES + 100
    profit2 = (range_no_ask - 1) - lower_no_ask - higher_yes_ask 
    
    return {
        'profit1': profit1,
        'profit2': profit2
    }

def init_profit_csv(csv_filename, num_ranges=5):
    """Initialize CSV file with headers for multiple ranges"""
    file_exists = os.path.exists(csv_filename)
    with open(csv_filename, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            headers = ['Time']
            for i in range(1, num_ranges + 1):
                headers.extend([
                    f'Range{i} Profit 1 (Range YES limit)',
                    f'Range{i} Profit 2 (Range NO limit)'
                ])
            writer.writerow(headers)
    print(f"📝 Profit data will be logged to: {csv_filename}")

def log_profits_to_csv(csv_filename, all_profits):
    """Append profit values for all ranges to CSV file
    
    Args:
        csv_filename: Path to CSV file
        all_profits: List of dicts, each containing profit1, profit2
    """
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = [current_time]
        for profits in all_profits:
            if profits:
                row.extend([
                    f"{profits.get('profit1', 0):.2f}",
                    f"{profits.get('profit2', 0):.2f}"
                ])
            else:
                row.extend(['0.00', '0.00'])
        
        with open(csv_filename, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(row)
    except Exception as e:
        print(f"⚠️  Error writing to CSV: {e}")



import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
from datetime import datetime
import threading
import time

class ProfitGraph:
    """Live-updating time series graph for profit values"""
    
    def __init__(self, window_seconds=300, update_interval=1000):
        """
        Initialize the profit graph
        
        Args:
            window_seconds: Rolling window size in seconds (default: 300 = 5 minutes)
            update_interval: Animation update interval in milliseconds (default: 1000 = 1 second)
        """
        self.window_seconds = window_seconds
        self.update_interval = update_interval
        
        # Data storage (rolling windows)
        self.timestamps = deque(maxlen=window_seconds)
        self.profit1 = deque(maxlen=window_seconds)
        self.profit2 = deque(maxlen=window_seconds)
        self.profit3 = deque(maxlen=window_seconds)
        self.profit4 = deque(maxlen=window_seconds)
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Setup the plot
        self.fig, self.ax = plt.subplots(figsize=(12, 6))
        self.lines = {}
        
        # Initialize lines
        self.lines['profit1'] = self.ax.plot([], [], label='Profit 1 (Range YES overpriced)', color='blue', linewidth=1.5)[0]
        self.lines['profit2'] = self.ax.plot([], [], label='Profit 2 (Range YES underpriced)', color='green', linewidth=1.5)[0]
        self.lines['profit3'] = self.ax.plot([], [], label='Profit 3 (Range NO overpriced)', color='red', linewidth=1.5)[0]
        self.lines['profit4'] = self.ax.plot([], [], label='Profit 4 (Range NO underpriced)', color='orange', linewidth=1.5)[0]
        
        # Configure axes
        self.ax.set_xlabel('Time (seconds)', fontsize=10)
        self.ax.set_ylabel('Profit (cents)', fontsize=10)
        self.ax.set_title('Profit Opportunities Over Time', fontsize=12, fontweight='bold')
        self.ax.grid(True, alpha=0.3)
        self.ax.legend(loc='best', fontsize=9)
        self.ax.axhline(y=0, color='black', linestyle='--', linewidth=0.5, alpha=0.5)
        
        # Start time for relative timestamps
        self.start_time = time.time()
        
        # Animation
        self.ani = None
        self.running = False
        
    def add_data_point(self, profit1, profit2, profit3, profit4):
        """
        Add a new data point to the graph
        
        Args:
            profit1: Profit 1 value (cents)
            profit2: Profit 2 value (cents)
            profit3: Profit 3 value (cents)
            profit4: Profit 4 value (cents)
        """
        current_time = time.time()
        elapsed = current_time - self.start_time
        
        with self.lock:
            self.timestamps.append(elapsed)
            self.profit1.append(profit1)
            self.profit2.append(profit2)
            self.profit3.append(profit3)
            self.profit4.append(profit4)
    
    def _update_graph(self, frame):
        """Update the graph with current data (called by animation)"""
        with self.lock:
            if len(self.timestamps) == 0:
                return self.lines.values()
            
            # Convert deques to lists for plotting
            timestamps = list(self.timestamps)
            profit1_data = list(self.profit1)
            profit2_data = list(self.profit2)
            profit3_data = list(self.profit3)
            profit4_data = list(self.profit4)
        
        # Update lines
        self.lines['profit1'].set_data(timestamps, profit1_data)
        self.lines['profit2'].set_data(timestamps, profit2_data)
        self.lines['profit3'].set_data(timestamps, profit3_data)
        self.lines['profit4'].set_data(timestamps, profit4_data)
        
        # Update axes limits
        if len(timestamps) > 0:
            self.ax.set_xlim(max(0, timestamps[-1] - self.window_seconds), timestamps[-1] + 1)
            
            # Y-axis: show all data with some padding
            all_profits = profit1_data + profit2_data + profit3_data + profit4_data
            if all_profits:
                y_min = min(all_profits) - 5
                y_max = max(all_profits) + 5
                self.ax.set_ylim(y_min, y_max)
        
        return list(self.lines.values())
    
    def start(self):
        """Start the live graph animation"""
        if not self.running:
            self.running = True
            self.ani = animation.FuncAnimation(
                self.fig, 
                self._update_graph, 
                interval=self.update_interval,
                blit=True,
                cache_frame_data=False
            )
            plt.show(block=False)
    
    def stop(self):
        """Stop the animation"""
        if self.ani:
            self.ani.event_source.stop()
            self.running = False
    
    def close(self):
        """Close the graph window"""
        self.stop()
        plt.close(self.fig)


def main():
    """Example usage"""
    import random
    
    graph = ProfitGraph(window_seconds=60, update_interval=1000)
    graph.start()
    
    print("Graph started. Adding sample data...")
    print("Close the graph window to stop.")
    
    try:
        # Simulate data
        for i in range(100):
            profit1 = random.uniform(-10, 10)
            profit2 = random.uniform(-10, 10)
            profit3 = random.uniform(-10, 10)
            profit4 = random.uniform(-10, 10)
            
            graph.add_data_point(profit1, profit2, profit3, profit4)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        graph.close()


if __name__ == "__main__":
    main()


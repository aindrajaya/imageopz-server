#!/usr/bin/env python3
"""
Real-time memory monitor for ImageOpz processes.
Tracks RAM usage of API, worker, and cleanup scheduler to detect OOM issues.

Usage:
    python monitor_memory.py                    # Monitor all imageopz processes
    python monitor_memory.py --duration 300     # Monitor for 5 minutes
    python monitor_memory.py --pid 12345        # Monitor specific process
    python monitor_memory.py --threshold 800    # Alert when usage exceeds 800MB
"""

import os
import sys
import psutil
import argparse
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("imageopz.monitor")


class MemoryMonitor:
    """Monitor memory usage of ImageOpz processes"""
    
    # Memory thresholds (MB)
    WARNING_THRESHOLD_MB = 700   # Warn at 700MB (leaving 300MB buffer for 1GB VPS)
    CRITICAL_THRESHOLD_MB = 950  # Critical at 950MB (only 50MB left)
    
    def __init__(self, target_pid: Optional[int] = None, duration_seconds: Optional[int] = None, 
                 threshold_mb: Optional[int] = None):
        """
        Initialize monitor.
        
        Args:
            target_pid: Specific process to monitor (None = all imageopz processes)
            duration_seconds: Monitor duration in seconds (None = infinite)
            threshold_mb: Custom alert threshold in MB
        """
        self.target_pid = target_pid
        self.duration_seconds = duration_seconds
        self.alert_threshold_mb = threshold_mb or self.WARNING_THRESHOLD_MB
        self.start_time = time.time()
        self.peak_memory_mb = 0.0
        self.peak_memory_time = None
        
    def find_imageopz_processes(self) -> Dict[int, Dict]:
        """Find all ImageOpz-related processes (API, worker, scheduler)"""
        processes = {}
        
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
                try:
                    cmdline = ' '.join(proc.cmdline()) if proc.cmdline() else ''
                    
                    # Match FastAPI/Uvicorn processes (API)
                    if 'imageopz' in cmdline.lower() and ('run.py' in cmdline or 'uvicorn' in cmdline):
                        processes[proc.pid] = {
                            'name': 'API (FastAPI)',
                            'cmdline': cmdline[:100],
                            'type': 'api'
                        }
                    
                    # Match worker processes
                    elif 'imageopz' in cmdline.lower() and 'run_worker.py' in cmdline:
                        processes[proc.pid] = {
                            'name': 'Worker',
                            'cmdline': cmdline[:100],
                            'type': 'worker'
                        }
                    
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        
        except Exception as e:
            logger.error(f"Error finding processes: {e}")
        
        return processes
    
    def format_memory(self, mb: float) -> str:
        """Format memory size for display"""
        if mb >= 1024:
            return f"{mb/1024:.2f}GB"
        return f"{mb:.1f}MB"
    
    def get_process_memory(self, pid: int) -> Optional[float]:
        """Get process memory usage in MB"""
        try:
            proc = psutil.Process(pid)
            memory_info = proc.memory_info()
            # RSS (Resident Set Size) = actual physical memory used
            return memory_info.rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        except Exception:
            return None
    
    def get_system_memory(self) -> Dict:
        """Get system-wide memory stats"""
        try:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return {
                'total': mem.total / (1024**3),      # GB
                'used': mem.used / (1024**3),        # GB
                'available': mem.available / (1024**3),
                'percent': mem.percent,
                'swap_used': swap.used / (1024**3)
            }
        except Exception:
            return {}
    
    def run_single_probe(self):
        """Run a single monitoring snapshot"""
        processes = self.find_imageopz_processes()
        
        if self.target_pid:
            # Filter to specific PID
            if self.target_pid in processes:
                processes = {self.target_pid: processes[self.target_pid]}
            else:
                # Try direct lookup
                mem = self.get_process_memory(self.target_pid)
                if mem:
                    processes = {self.target_pid: {'name': 'Target Process', 'type': 'unknown'}}
                else:
                    logger.error(f"Process PID {self.target_pid} not found")
                    return
        
        if not processes:
            logger.warning("No ImageOpz processes found (API/worker may not be running)")
            return
        
        # Get system memory
        sys_mem = self.get_system_memory()
        
        # Clear screen (optional, comment out if not needed)
        # os.system('clear' if os.name == 'posix' else 'cls')
        
        print("\n" + "="*80)
        print(f"ImageOpz Memory Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        if sys_mem:
            used_percent = sys_mem.get('percent', 0)
            color_code = '\033[91m' if used_percent > 80 else '\033[93m' if used_percent > 60 else '\033[92m'
            reset_code = '\033[0m'
            print(f"\n📊 System Memory:")
            print(f"   Total: {sys_mem['total']:.2f}GB")
            print(f"   Used:  {color_code}{sys_mem['used']:.2f}GB ({used_percent:.1f}%){reset_code}")
            print(f"   Available: {sys_mem['available']:.2f}GB")
            if sys_mem['swap_used'] > 0:
                print(f"   Swap Used: {sys_mem['swap_used']:.2f}GB ⚠️ (process swapping detected)")
        
        print(f"\n🔍 ImageOpz Processes:")
        
        total_imageopz_mb = 0.0
        
        for pid, proc_info in sorted(processes.items()):
            mem_mb = self.get_process_memory(pid)
            
            if mem_mb is None:
                logger.warning(f"  [{pid}] {proc_info['name']}: (access denied)")
                continue
            
            total_imageopz_mb += mem_mb
            
            # Color code: green < 500MB, yellow < 800MB, red > 800MB
            if mem_mb > self.alert_threshold_mb:
                color = '\033[91m'  # Red
                emoji = '🔴'
            elif mem_mb > 500:
                color = '\033[93m'  # Yellow
                emoji = '🟡'
            else:
                color = '\033[92m'  # Green
                emoji = '🟢'
            
            reset = '\033[0m'
            
            print(f"  {emoji} [{pid}] {proc_info['name']:<20} {color}{self.format_memory(mem_mb):>10}{reset}")
            
            # Update peak memory
            if mem_mb > self.peak_memory_mb:
                self.peak_memory_mb = mem_mb
                self.peak_memory_time = datetime.now()
        
        print(f"\n📈 Total ImageOpz Memory: {self.format_memory(total_imageopz_mb)}")
        if self.peak_memory_time:
            print(f"   Peak: {self.format_memory(self.peak_memory_mb)} at {self.peak_memory_time.strftime('%H:%M:%S')}")
        
        # Warnings
        if sys_mem:
            available_mb = sys_mem['available'] * 1024
            if available_mb < 200:
                print(f"\n⚠️  CRITICAL: Only {self.format_memory(available_mb)} available! OOM risk high!")
            elif available_mb < 400:
                print(f"\n⚠️  WARNING: Only {self.format_memory(available_mb)} available")
        
        if total_imageopz_mb > self.alert_threshold_mb:
            print(f"\n🚨 ALERT: ImageOpz using {self.format_memory(total_imageopz_mb)} (threshold: {self.format_memory(self.alert_threshold_mb)})")
        
        print("\n" + "="*80)
    
    def monitor_continuous(self, interval_seconds: int = 5):
        """Run continuous monitoring"""
        logger.info(f"Starting memory monitor (interval: {interval_seconds}s, threshold: {self.format_memory(self.alert_threshold_mb)})")
        if self.duration_seconds:
            logger.info(f"Will run for {self.duration_seconds} seconds")
        
        try:
            while True:
                self.run_single_probe()
                
                # Check if duration exceeded
                if self.duration_seconds:
                    elapsed = time.time() - self.start_time
                    if elapsed >= self.duration_seconds:
                        logger.info(f"Monitor duration ({self.duration_seconds}s) exceeded. Exiting.")
                        break
                
                time.sleep(interval_seconds)
        
        except KeyboardInterrupt:
            print("\n\n" + "="*80)
            print("Memory Monitor Stopped")
            print("="*80)
            if self.peak_memory_mb > 0:
                print(f"\nSummary:")
                print(f"  Peak Memory: {self.format_memory(self.peak_memory_mb)} at {self.peak_memory_time.strftime('%H:%M:%S')}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description="Monitor real-time memory usage of ImageOpz processes"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Monitor duration in seconds (default: infinite)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Refresh interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="Monitor specific process PID (default: all imageopz processes)"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=700,
        help="Memory alert threshold in MB (default: 700)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (no continuous monitoring)"
    )
    
    args = parser.parse_args()
    
    monitor = MemoryMonitor(
        target_pid=args.pid,
        duration_seconds=args.duration,
        threshold_mb=args.threshold
    )
    
    if args.once:
        monitor.run_single_probe()
    else:
        monitor.monitor_continuous(interval_seconds=args.interval)


if __name__ == "__main__":
    main()

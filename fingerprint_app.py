#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fingerprint Device Connection Test Flask Application
Supports two systems: Push and Normal
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
import sqlite3
import time
import json
import os
from datetime import datetime
import threading
import queue
import uuid

app = Flask(__name__)
app.secret_key = 'fingerprint_test_secret_key_2024'

# Global variables
devices = []
normal_system = None
push_system = None
db_manager = None
push_data_log = []

class FingerprintDevice:
    """Fingerprint Device Class"""
    
    def __init__(self, device_id, ip_address, port=4370, device_name=""):
        self.device_id = device_id
        self.ip_address = ip_address
        self.port = port
        self.device_name = device_name or f"Device {device_id}"
        self.is_connected = False
        self.last_communication = None
        self.connection_type = "Normal"
        
    def __str__(self):
        return f"{self.device_name} ({self.ip_address}:{self.port})"
    
    def test_connection(self):
        """Test device connection"""
        try:
            # Simulate connection test
            time.sleep(1)  # Simulate connection time
            
            # Simulate successful connection
            self.is_connected = True
            self.last_communication = datetime.now()
            return True
            
        except Exception as e:
            self.is_connected = False
            return False
    
    def get_attendance_data(self):
        """Get attendance data from device"""
        if not self.is_connected:
            return []
        
        try:
            time.sleep(1)  # Simulate data retrieval time
            
            # Simulate attendance data
            attendance_data = [
                {
                    'employee_id': '123',
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'type': 'check_in',
                    'device_id': self.device_id
                },
                {
                    'employee_id': '456',
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'type': 'check_out',
                    'device_id': self.device_id
                }
            ]
            
            return attendance_data
            
        except Exception as e:
            return []

class PushSystem:
    """Push System for Fingerprint"""
    
    def __init__(self):
        self.devices = {}
        self.push_queue = queue.Queue()
        self.is_running = False
        self.push_thread = None
        
    def add_device(self, device):
        """Add device to system"""
        device.connection_type = "push"
        self.devices[device.device_id] = device
        
    def start_push_system(self):
        """Start Push system"""
        if self.is_running:
            return False
        
        self.is_running = True
        self.push_thread = threading.Thread(target=self._push_worker)
        self.push_thread.daemon = True
        self.push_thread.start()
        return True
    
    def stop_push_system(self):
        """Stop Push system"""
        self.is_running = False
        if self.push_thread:
            self.push_thread.join(timeout=2)
    
    def _push_worker(self):
        """Push worker thread"""
        while self.is_running:
            try:
                # Simulate receiving Push data
                for device_id, device in self.devices.items():
                    if device.is_connected:
                        # Simulate receiving new data
                        if time.time() % 10 < 1:  # Every 10 seconds
                            push_data = {
                                'id': str(uuid.uuid4()),
                                'device_id': device_id,
                                'device_name': device.device_name,
                                'employee_id': f'EMP{int(time.time()) % 1000}',
                                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'type': 'check_in' if int(time.time()) % 2 == 0 else 'check_out'
                            }
                            self.push_queue.put(push_data)
                            push_data_log.append(push_data)
                            # Keep only last 100 records
                            if len(push_data_log) > 100:
                                push_data_log.pop(0)
                
                time.sleep(1)
                
            except Exception as e:
                time.sleep(5)
    
    def get_push_data(self):
        """Get available Push data"""
        data = []
        while not self.push_queue.empty():
            try:
                data.append(self.push_queue.get_nowait())
            except queue.Empty:
                break
        return data

class NormalSystem:
    """Normal System for Fingerprint"""
    
    def __init__(self):
        self.devices = {}
    
    def add_device(self, device):
        """Add device to system"""
        device.connection_type = "Normal"
        self.devices[device.device_id] = device
    
    def test_all_connections(self):
        """Test all connections"""
        results = {}
        
        for device_id, device in self.devices.items():
            results[device_id] = device.test_connection()
        
        return results
    
    def get_all_attendance_data(self):
        """Get attendance data from all devices"""
        all_data = []
        
        for device_id, device in self.devices.items():
            data = device.get_attendance_data()
            all_data.extend(data)
        
        return all_data

class DatabaseManager:
    """Database Manager"""
    
    def __init__(self, db_path='hr_system.db'):
        self.db_path = db_path
    
    def get_employees(self):
        """Get employees data"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, employee_number, name, department, position 
                FROM employees 
                ORDER BY employee_number
            """)
            
            employees = cursor.fetchall()
            pass # conn.close() removed to prevent leak in Flask g
            
            return employees
            
        except Exception as e:
            return []
    
    def save_attendance_data(self, attendance_data):
        """Save attendance data"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for record in attendance_data:
                cursor.execute("""
                    INSERT INTO attendance (employee_id, timestamp, type, device_id)
                    VALUES (?, ?, ?, ?)
                """, (record['employee_id'], record['timestamp'], record['type'], record['device_id']))
            
            conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
            
            return True
            
        except Exception as e:
            return False

def setup_devices():
    """Setup devices"""
    global devices
    
    devices = []
    
    # Add virtual devices
    devices.append(FingerprintDevice(1, "192.168.1.100", 4370, "Main Fingerprint Device"))
    devices.append(FingerprintDevice(2, "192.168.1.101", 4370, "Secondary Fingerprint Device"))
    devices.append(FingerprintDevice(3, "192.168.1.102", 4370, "Admin Fingerprint Device"))
    
    return devices

def initialize_systems():
    """Initialize all systems"""
    global normal_system, push_system, db_manager
    
    normal_system = NormalSystem()
    push_system = PushSystem()
    db_manager = DatabaseManager()
    
    # Setup devices if not already done
    if not devices:
        setup_devices()
    
    # Add devices to systems
    for device in devices:
        normal_system.add_device(device)
        push_system.add_device(device)

# Initialize systems on startup
initialize_systems()

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('fingerprint_dashboard.html', 
                         devices=devices,
                         push_running=push_system.is_running if push_system else False)

@app.route('/setup_devices')
def setup_devices_route():
    """Setup devices route"""
    global devices
    devices = setup_devices()
    
    # Re-add to systems
    if normal_system and push_system:
        normal_system.devices.clear()
        push_system.devices.clear()
        for device in devices:
            normal_system.add_device(device)
            push_system.add_device(device)
    
    flash('Devices setup completed successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/test_connections')
def test_connections():
    """Test all connections"""
    if not normal_system:
        return jsonify({'error': 'System not initialized'})
    
    results = normal_system.test_all_connections()
    return jsonify({
        'success': True,
        'results': results,
        'message': f'Tested {len(results)} devices'
    })

@app.route('/start_push')
def start_push():
    """Start Push system"""
    if not push_system:
        return jsonify({'error': 'Push system not initialized'})
    
    success = push_system.start_push_system()
    return jsonify({
        'success': success,
        'message': 'Push system started' if success else 'Push system already running'
    })

@app.route('/stop_push')
def stop_push():
    """Stop Push system"""
    if not push_system:
        return jsonify({'error': 'Push system not initialized'})
    
    push_system.stop_push_system()
    return jsonify({
        'success': True,
        'message': 'Push system stopped'
    })

@app.route('/get_attendance')
def get_attendance():
    """Get attendance data"""
    if not normal_system or not push_system:
        return jsonify({'error': 'Systems not initialized'})
    
    # Get data from both systems
    normal_data = normal_system.get_all_attendance_data()
    push_data = push_system.get_push_data()
    
    # Save to database
    if db_manager:
        if normal_data:
            db_manager.save_attendance_data(normal_data)
        if push_data:
            # Convert push data format
            formatted_push_data = []
            for record in push_data:
                formatted_push_data.append({
                    'employee_id': record['employee_id'],
                    'timestamp': record['timestamp'],
                    'type': record['type'],
                    'device_id': record['device_id']
                })
            db_manager.save_attendance_data(formatted_push_data)
    
    return jsonify({
        'success': True,
        'normal_data': normal_data,
        'push_data': push_data,
        'total_records': len(normal_data) + len(push_data)
    })

@app.route('/get_employees')
def get_employees():
    """Get employees data"""
    if not db_manager:
        return jsonify({'error': 'Database manager not initialized'})
    
    employees = db_manager.get_employees()
    return jsonify({
        'success': True,
        'employees': employees,
        'total': len(employees)
    })

@app.route('/get_device_status')
def get_device_status():
    """Get device status"""
    device_status = []
    
    for device in devices:
        status = {
            'id': device.device_id,
            'name': device.device_name,
            'ip': device.ip_address,
            'port': device.port,
            'connected': device.is_connected,
            'type': device.connection_type,
            'last_communication': device.last_communication.strftime('%Y-%m-%d %H:%M:%S') if device.last_communication else 'Not Available'
        }
        device_status.append(status)
    
    return jsonify({
        'success': True,
        'devices': device_status
    })

@app.route('/get_push_log')
def get_push_log():
    """Get Push data log"""
    return jsonify({
        'success': True,
        'push_log': push_data_log[-20:],  # Last 20 records
        'total': len(push_data_log)
    })

@app.route('/api/status')
def api_status():
    """API status endpoint"""
    return jsonify({
        'status': 'running',
        'devices_count': len(devices),
        'push_running': push_system.is_running if push_system else False,
        'normal_system': normal_system is not None,
        'push_system': push_system is not None,
        'db_manager': db_manager is not None
    })

if __name__ == '__main__':
    print("🚀 Starting Fingerprint Device Connection Test Flask Application...")
    print("📱 Web interface available at: http://localhost:5000")
    print("🔧 API endpoints available at: http://localhost:5000/api/")
    app.run(debug=True, host='0.0.0.0', port=5000)

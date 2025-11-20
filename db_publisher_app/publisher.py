import os
import time
import json
import mysql.connector
import paho.mqtt.client as mqtt
from datetime import datetime
import logging # <-- Import the logging module

# --- Logging Setup ---
# Set up basic configuration for logging
logging.basicConfig(
    level=logging.INFO, # Display INFO, WARNING, ERROR, CRITICAL messages
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Alias the logger for convenience
logger = logging.getLogger(__name__)

# --- Configuration (Defaults from docker-compose.yml) ---
DB_HOST = os.getenv("DB_HOST", "SITH-MySQL") 
DB_NAME = os.getenv("DB_NAME", "SITH")
DB_USER = os.getenv("DB_USER", "SITH")
DB_PASS = os.getenv("DB_PASS", "SITH")
MQTT_HOST = os.getenv("MQTT_HOST", "SITH-MQTT-Broker")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 5))

TOPIC_PREFIX = "rovers/" 
# File to store the list of module names from the previous run
TRACKING_FILE = "/app/published_modules.json"

# --- Mosquitto Client Setup ---
def on_connect(client, userdata, flags, rc, properties):
    # Use logger.info instead of print
    logger.info(f"Connected to MQTT broker: {mqtt.connack_string(rc)}")

# Use logger.info instead of print
logger.info(f"Connecting to MQTT broker... {MQTT_HOST}:{MQTT_PORT}")
# mqtt_client = mqtt.Client()
mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
mqtt_client.loop_start()

# --- Topic Tracking Functions ---
def load_previous_modules():
    """Loads the set of module names published in the previous run."""
    if os.path.exists(TRACKING_FILE):
        with open(TRACKING_FILE, 'r') as f:
            try:
                # Load the list of names and convert to a set for fast lookup
                return set(json.load(f))
            except json.JSONDecodeError:
                # Use logger.warning instead of print
                logger.warning(f"Could not decode {TRACKING_FILE}. Starting fresh.")
    return set()

def save_current_modules(current_module_names):
    """Saves the set of currently published module names."""
    with open(TRACKING_FILE, 'w') as f:
        # Save the set back as a list
        json.dump(list(current_module_names), f)

# --- Core Logic ---
def read_and_publish_data():
    
    # Load the module names published in the last successful run
    previous_module_names = load_previous_modules()
    current_module_names = set()

    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        cur = conn.cursor(dictionary=True)

        query = "SELECT roverID, data FROM rovers;"
        cur.execute(query)
        
        results = cur.fetchall()
        
        published_count = 0
        
        if results:
            for record in results:
                
                module_name = record.get('roverID')
                module_value = record.get('data')

                if not module_name or module_value is None:
                    # Added a debug log for skipped records
                    logger.debug(f"Skipping invalid record: roverID={module_name}, data={module_value}")
                    continue # Skip invalid records

                # 1. Prepare topic and payload
                module_name_clean = str(module_name).strip().replace(' ', '_').replace('/', '_')
                unique_topic = f"{TOPIC_PREFIX}{module_name_clean}"
                payload = str(module_value)
                
                # 2. Publish (Update/Retain) the current value
                # We use retain=True here to ensure the topic persists on the broker
                mqtt_client.publish(unique_topic, payload, qos=1, retain=True)
                published_count += 1

                # Added a debug log for each publication
                logger.debug(f"Published topic: {unique_topic}, payload: {payload}")
                
                # 3. Track the module name for the current run
                current_module_names.add(module_name)
            
            # Use logger.info instead of print
            logger.info(f"Published/Updated {published_count} individual values.")
        else:
            logger.info("No records found in the 'rovers' table to publish.")
        
        cur.close()
        conn.close()

        # --- Topic Clearing Logic ---
        # Find topics that were published last time but are missing now
        topics_to_clear = previous_module_names - current_module_names
        cleared_count = 0
        
        for module_name_to_clear in topics_to_clear:
            module_name_clean = str(module_name_to_clear).strip().replace(' ', '_').replace('/', '_')
            clear_topic = f"{TOPIC_PREFIX}{module_name_clean}"
            
            # Publish with an empty payload and retain=True to clear the retained message
            mqtt_client.publish(clear_topic, payload=None, qos=1, retain=True)
            cleared_count += 1
            
            # Added a debug log for each cleared topic
            logger.debug(f"Cleared retained topic: {clear_topic}")
            
        if cleared_count > 0:
            # Use logger.info instead of print
            logger.info(f"Cleared {cleared_count} old retained topics.")


    except Exception as e:
        # Replaced custom print error handling with logger.error
        error_message = str(e)
        if "'rovers' doesn't exist" in error_message:
             logger.error("The 'rovers' table is missing from the database.")
        elif "Unknown column" in error_message:
             logger.error("Missing required column(s) in the 'rovers' table.")
        else:
             logger.error(f"Could not read database or publish to MQTT: {e}", exc_info=True) # exc_info=True prints the traceback
             
        # IMPORTANT: If the run failed, we DO NOT update the tracking file, 
        # so we will re-attempt clearing on the next successful run.
        return 


    # --- Save State ---
    # Only save the new list if the database read and publishing was successful
    save_current_modules(current_module_names)


if __name__ == "__main__":
    # Use logger.info instead of print
    logger.info("DB Publisher service started. Polling database...")
    # Give the database a moment to fully initialize
    time.sleep(10) 
    while True:
        read_and_publish_data()
        time.sleep(POLL_INTERVAL)
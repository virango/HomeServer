import threading
import time
import socket
import json
import paho.mqtt.client as mqtt
import datetime
import os

def log(msg, level=0):
    if level <= current_log_level:
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# Konstanten
BATTERY_OUTPUT_MIN = 80
BATTERY_OUTPUT_MAX = 620
REACTIVE_POWER_FACTOR = 0.8

# Globale Variablen
broker_ip = os.getenv("MQTT_BROKER_IP", "192.168.178.3")
broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
smartmeter_ip = os.getenv("SMARTMETER_IP", "192.168.178.53")
smartmeter_port = int(os.getenv("SMARTMETER_PORT", "12345"))

phase1 = -1
phase2 = -1
phase3 = -1
current_output_power = 0
battery_output_power = -1
battery_level = -1
io_meter_consumption = None
io_meter_connection = 0
smartmeter_mode = 1
current_log_level = 2 # 0=Error, 1=Info, 2=Debug

mqtt_publish_mutex = threading.Lock()
calc_data_mutex = threading.Lock()

def on_connect(client, userdata, flags, reason_code, properties):
    log(f"[MQTT] Connected with code {reason_code}", 1)
    client.subscribe("hame_energy/HMA-1/device/2419720d2e06/ctrl")
    client.subscribe("homeassistant/status")
    client.subscribe("mars/log_level")
    client.subscribe("mars/smartmeter")
    client.subscribe("homeassistant/power/io_meter/value")
    client.subscribe("homeassistant/power/io_meter/connection")
    log("[HomeAssistant] Sending discovery topics after connection", 1)
    publish_homeassistant_discovery(client)

def on_connect_fail(client, userdata):
    log("[MQTT] Connection failed", 0)

def on_disconnect(client, userdata, reason_code, properties=None, packet_from_broker=None):
    log(f"[MQTT] Disconnected. Reason code: {reason_code}", 1)

def publish_homeassistant_discovery(mqttc):
    try:
        base_device = {
            "identifiers": ["smartmeter_xyz"],
            "name": "Smartmeter",
            "manufacturer": "Custom",
            "model": "MQTT Meter"
        }

        sensors = [
            ("homeassistant/sensor/smartmeter_phase1/config", {
                "name": "Smartmeter Phase 1",
                "state_topic": "smartmeter/values/phase1",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
                "unique_id": "smartmeter_phase1",
                "device": base_device
            }),
            ("homeassistant/sensor/smartmeter_phase2/config", {
                "name": "Smartmeter Phase 2",
                "state_topic": "smartmeter/values/phase2",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
                "unique_id": "smartmeter_phase2",
                "device": base_device
            }),
            ("homeassistant/sensor/smartmeter_phase3/config", {
                "name": "Smartmeter Phase 3",
                "state_topic": "smartmeter/values/phase3",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
                "unique_id": "smartmeter_phase3",
                "device": base_device
            })
        ]

        for topic, payload in sensors:
            mqttc.publish(topic, json.dumps(payload), retain=True)
            log(f"[HomeAssistant] Discovery published: {topic}", 1)
    except Exception as e:
        log(f"[HomeAssistant] Error sending discovery topics: {e}", 0)

def on_message(client, userdata, msg):
    try:
        global io_meter_consumption, io_meter_connection, smartmeter_mode, current_log_level
        topic = msg.topic
        payload = msg.payload.decode().strip()
        if topic.startswith("hame_energy"):
            process_battery_data(payload)
        elif topic == "homeassistant/power/io_meter/value":
            try:
                io_meter_consumption = float(payload)
                log(f"[IoMeter] Consumption value: {io_meter_consumption}W", 2)
                calculate_battery_output()
            except ValueError:
                log(f"[IoMeter] Invalid consumption payload: {payload}", 0)
        elif topic == "homeassistant/power/io_meter/connection":
            try:
                io_meter_connection = int(payload)
                log(f"[IoMeter] Connection state: {io_meter_connection}", 2)
                calculate_battery_output()
            except ValueError:
                log(f"[IoMeter] Invalid connection payload: {payload}", 0)
        elif topic == "mars/smartmeter":
            try:
                mode = int(payload)
                if mode in (1, 2):
                    smartmeter_mode = mode
                    log(f"[MQTT] Smartmeter mode set to {smartmeter_mode}", 1)
                else:
                    log(f"[MQTT] Invalid smartmeter mode received: {payload}", 0)
            except ValueError:
                log(f"[MQTT] Invalid smartmeter mode payload: {payload}", 0)
        elif topic == "homeassistant/status" and payload == "online":
            log("[HomeAssistant] Online received — sending discovery topics", 1)
            publish_homeassistant_discovery(client)
        elif topic == "mars/log_level":
            try:
                current_log_level = int(payload)
                log(f"Log level set to {current_log_level}", 1)
            except ValueError:
                log("Invalid log level received", 0)
    except Exception as e:
        log(f"[MQTT] Error processing message: {e}", 0)

def mqtt_init():
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqttc.on_connect = on_connect
    mqttc.on_connect_fail = on_connect_fail
    mqttc.on_disconnect = on_disconnect
    mqttc.on_message = on_message
    mqttc.connect(broker_ip, broker_port, 60)
    mqttc.loop_start()
    return mqttc

def publish_battery_data_request(mqttc):
    while True:
        try:
            with mqtt_publish_mutex:
                if mqttc.is_connected():
                    mqttc.publish("hame_energy/HMA-1/App/2419720d2e06/ctrl", "cd=01")
            time.sleep(5)
        except Exception as e:
            log(f"[Error] Battery query: {e}", 0)
            time.sleep(5)

def publish_smartmeter_values(mqttc):
    while True:
        try:
            with mqtt_publish_mutex:
                if mqttc.is_connected():
                    mqttc.publish("smartmeter/values/phase1", str(phase1))
                    mqttc.publish("smartmeter/values/phase2", str(phase2))
                    mqttc.publish("smartmeter/values/phase3", str(phase3))
            time.sleep(5)
        except Exception as e:
            log(f"[Error] Sending smartmeter values: {e}", 0)
            time.sleep(5)

def smartmeter_init():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((smartmeter_ip, smartmeter_port))
        log("[Smartmeter] Connection established", 1)
        sock.sendall(b"hello\n")
        return sock
    except Exception as e:
        log(f"[Smartmeter] Connection error: {e}", 0)
        return None

def smartmeter_receive_data(sock):
    while True:
        try:
            data = sock.recv(1024).decode().strip()
            if data:
                process_smartmeter_data(data)
            time.sleep(5)
        except Exception as e:
            log(f"[Smartmeter] Error receiving: {e}", 0)
            sock = smartmeter_init()
            time.sleep(10)

def process_smartmeter_data(data):
    global phase1, phase2, phase3
    try:
        lines = data.split("HM:")
        for line in lines:
            if line.strip():
                values = line.strip().split("|")
                if len(values) == 3:
                    phase1 = int(values[0])
                    phase2 = int(values[1])
                    phase3 = int(values[2])
                    log(f"[Smartmeter data] Phase 1: {phase1}W, Phase 2: {phase2}W, Phase 3: {phase3}W", 2)
                    calculate_battery_output()
    except Exception as e:
        log(f"[Smartmeter] Error processing: {e}", 0)

def process_battery_data(data):
    global battery_level, current_output_power
    try:
        g1 = -1
        g2 = -1
        with calc_data_mutex:
            for pair in data.split(","):
                if "=" in pair:
                    key, value = pair.strip().split("=")
                    if key == "pe": battery_level = int(value)
                    elif key == "g1": g1 = int(value)
                    elif key == "g2": g2 = int(value)
            if g1 != -1 and g2 != -1:        
                current_output_power = g1 + g2
                log(f"[Battery data] Level: {battery_level}%, Output Power: {current_output_power}W", 2)
                calculate_battery_output()
    except Exception as e:
        log(f"[Battery] Error processing: {e}", 0)


def calculate_battery_output():
    if smartmeter_mode == 2:
        calculate_battery_output_ct001()
    else:
        calculate_battery_output_iometer()


def calculate_max_battery_output():
    if battery_level == -1 or battery_level > 20:
        return BATTERY_OUTPUT_MAX

    if battery_level <= 10:
        log(f"[Battery output] Battery level critically low: {battery_level}%. Max output set to {BATTERY_OUTPUT_MIN}W", 1)
        return BATTERY_OUTPUT_MIN

    factor = (20 - battery_level) / 10.0
    reduction = (factor ** 2) * (BATTERY_OUTPUT_MAX - BATTERY_OUTPUT_MIN)
    max_output = max(BATTERY_OUTPUT_MIN, BATTERY_OUTPUT_MAX - reduction)
    log(f"[Battery output] Battery level: {battery_level}%, Max output adjusted to {max_output}W", 2)
    return max_output


def calculate_battery_output_iometer():
    global battery_output_power
    try:
        with calc_data_mutex:
            max_battery_output = calculate_max_battery_output()

            if io_meter_connection != 1:
                battery_output_power = BATTERY_OUTPUT_MIN
                log(f"[Battery output] IO meter connection invalid ({io_meter_connection}). Forcing minimum output {battery_output_power}W", 1)
            elif io_meter_consumption is None:
                battery_output_power = BATTERY_OUTPUT_MIN
                log("[Battery output] IoMeter consumption unavailable. Forcing minimum output.", 1)
            elif io_meter_consumption <= 0:
                battery_output_power = BATTERY_OUTPUT_MIN
                log(f"[Battery output] IoMeter consumption non-positive ({io_meter_consumption}W). Forcing minimum output.", 2)
            else:
                adjusted_consumption = io_meter_consumption + current_output_power
                if adjusted_consumption <= 0:
                    battery_output_power = BATTERY_OUTPUT_MIN
                    log(f"[Battery output] Adjusted consumption <=0 ({adjusted_consumption}W). Forcing minimum output.", 2)
                else:
                    battery_output_power = int(adjusted_consumption)
                    log(f"[Battery output] Using IoMeter net consumption {io_meter_consumption}W and current battery output {current_output_power}W => adjusted load {adjusted_consumption}W => output {battery_output_power}W", 2)

            battery_output_power = max(BATTERY_OUTPUT_MIN, min(battery_output_power, max_battery_output))
    except Exception as e:
        log(f"[Calculation] Error: {e}", 0)


def calculate_battery_output_ct001():
    global battery_output_power
    try:
        with calc_data_mutex:
            max_battery_output = calculate_max_battery_output()

            if phase1 < 40 and phase2 < 100 and phase3 < 130:
                battery_output_power = 80 if phase2 < 40 else 100
                log(f"[Battery output] Base consumption detected. Setting to {battery_output_power}W", 1)
            else:
                battery_output_power = int((phase1 + phase2) * REACTIVE_POWER_FACTOR)
                phase3_reactive = int(phase3 * REACTIVE_POWER_FACTOR)
                delta = abs(current_output_power - phase3_reactive)
                battery_output_power += int(delta)
                log(f"[Battery output] Calculated output power: {battery_output_power}W (Current Output: {current_output_power}W)", 2)

            battery_output_power = max(BATTERY_OUTPUT_MIN, min(battery_output_power, max_battery_output))
    except Exception as e:
        log(f"[Calculation] Error: {e}", 0)

def publish_set_battery_output(mqttc):
    while True:
        try:
            with mqtt_publish_mutex:
                with calc_data_mutex:
                    if battery_output_power != -1:
                        now = datetime.datetime.now().astimezone()
                        weekday = now.weekday()  # 0=Monday, 6=Sunday
                        hour = now.hour
                        minute = now.minute

                        # Determine if 80W should be forced
                        is_weekend = weekday >= 5
                        force_80w = False
                        if is_weekend:
                            # Weekend: 23:00 to 7:30
                            if hour >= 23 or (hour < 7 or (hour == 7 and minute <= 30)):
                                force_80w = True
                        else:
                            # Weekdays: 23:00 to 5:00
                            if hour >= 23 or hour < 5:
                                force_80w = True

                        # Calculate flowing 2-minute slot
                        b1_dt = now
                        e1_dt = now + datetime.timedelta(minutes=2)
                        if e1_dt.date() != now.date() or (e1_dt.hour == 0 and e1_dt.minute == 0):
                            # clamp to end of current day
                            e1_dt = datetime.datetime(now.year, now.month, now.day, 23, 59)

                        b1 = b1_dt.strftime("%H:%M")
                        e1 = e1_dt.strftime("%H:%M")
                        if e1 == "23:59":
                            b2 = "00:01"
                        else:
                            b2 = e1
                        e2 = "23:59"

                        # Shutdown (no feed-in) daily 23:59-00:01
                        is_interrupt = (hour == 23 and minute >= 59) or (hour == 0 and minute < 1)
                        if is_interrupt:
                            v1 = 0
                            a1 = 0
                        else:
                            v1 = 80 if force_80w else int(battery_output_power)
                            a1 = 1

                        payload = f"cd=20,md=0,a1={a1},b1={b1},e1={e1},v1={v1},a2=1,b2={b2},e2={e2},v2=80,a3=0,b3=0:0,e3=23:59,v3=80"
                        mqttc.publish("hame_energy/HMA-1/App/2419720d2e06/ctrl", payload)
                        log(f"[Battery output] Published control command: {payload}", 2)
            time.sleep(20)
        except Exception as e:
            log(f"[Error] Battery setting: {e}", 0)
            time.sleep(10)

def run():
    while True:
        try:
            mqttc = mqtt_init()
            smartmeter = smartmeter_init()
            if not smartmeter:
                log("[Warning] Smartmeter not available. Retrying in 10 seconds.", 1)
                time.sleep(10)
                continue

            threads = [
                threading.Thread(target=publish_battery_data_request, args=(mqttc,)),
                threading.Thread(target=smartmeter_receive_data, args=(smartmeter,)),
                threading.Thread(target=publish_smartmeter_values, args=(mqttc,)),
                threading.Thread(target=publish_set_battery_output, args=(mqttc,))
            ]

            for t in threads:
                t.daemon = True
                t.start()

            while True:
                time.sleep(60)
        except Exception as e:
            log(f"[Critical error] Restarting main process: {e}", 0)
            time.sleep(10)

if __name__ == "__main__":
    run()

import threading
import time
import socket
import json
import paho.mqtt.client as mqtt
import datetime
import os

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# Konstanten
BATTERY_OUTPUT_MIN = 80
BATTERY_OUTPUT_MAX = 620
REACTIVE_POWER_FACTOR = 1.0

# Globale Variablen
broker_ip = os.getenv("MQTT_BROKER_IP", "192.168.178.3")
broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
smartmeter_ip = os.getenv("SMARTMETER_IP", "192.168.178.53")
smartmeter_port = int(os.getenv("SMARTMETER_PORT", "12345"))

phase1 = -1
phase2 = -1
phase3 = -1
pv1_input_power = 0
pv2_input_power = 0
current_output_power = 0
battery_output_power = -1

mqtt_publish_mutex = threading.Lock()
calc_data_mutex = threading.Lock()

def on_connect(client, userdata, flags, reason_code, properties):
    log(f"[MQTT] Verbunden mit Code {reason_code}")
    client.subscribe("hame_energy/HMA-1/device/2419720d2e06/ctrl")
    client.subscribe("powermeter_balkon/status/switch:0")
    client.subscribe("homeassistant/status")
    log("[HomeAssistant] Sende Discovery-Topics nach Verbindungsaufbau")
    publish_homeassistant_discovery(client)

def on_connect_fail(client, userdata):
    log("[MQTT] Verbindung fehlgeschlagen")

def on_disconnect(client, userdata, reason_code, properties=None, packet_from_broker=None):
    log(f"[MQTT] Verbindung getrennt. Reason Code: {reason_code}")

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
            log(f"[HomeAssistant] Discovery veröffentlicht: {topic}")
    except Exception as e:
        log(f"[HomeAssistant] Fehler beim Senden der Discovery-Topics: {e}")

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = msg.payload.decode().strip()
        if topic.startswith("hame_energy"):
            process_battery_data(payload)
        elif topic.startswith("powermeter_balkon"):
            process_output_power_data(payload)
        elif topic == "homeassistant/status" and payload == "online":
            log("[HomeAssistant] online empfangen — sende Discovery-Topics")
            publish_homeassistant_discovery(client)
    except Exception as e:
        log(f"[MQTT] Fehler beim Verarbeiten der Nachricht: {e}")

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
            log(f"[Fehler] Batterieabfrage: {e}")
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
            log(f"[Fehler] Smartmeter-Werte senden: {e}")
            time.sleep(5)

def smartmeter_init():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((smartmeter_ip, smartmeter_port))
        log("[Smartmeter] Verbindung hergestellt")
        sock.sendall(b"hello\n")
        return sock
    except Exception as e:
        log(f"[Smartmeter] Verbindungsfehler: {e}")
        return None

def smartmeter_receive_data(sock):
    while True:
        try:
            data = sock.recv(1024).decode().strip()
            if data:
                process_smartmeter_data(data)
            time.sleep(5)
        except Exception as e:
            log(f"[Smartmeter] Fehler beim Empfang: {e}")
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
                    calculate_battery_output()
    except Exception as e:
        log(f"[Smartmeter] Fehler bei Verarbeitung: {e}")

def process_battery_data(data):
    global pv1_input_power, pv2_input_power
    try:
        for pair in data.split(","):
            if "=" in pair:
                key, value = pair.strip().split("=")
                if key == "w1": pv1_input_power = int(value)
                elif key == "w2": pv2_input_power = int(value)
    except Exception as e:
        log(f"[Batterie] Fehler bei Verarbeitung: {e}")

def process_output_power_data(data):
    global current_output_power
    try:
        dict_data = json.loads(data)
        if "apower" in dict_data:
            current_output_power = int(dict_data["apower"] * -1)
    except Exception as e:
        log(f"[Leistung] Fehler beim Parsen: {e}")

def calculate_battery_output():
    global battery_output_power
    try:
        with calc_data_mutex:
            if phase1 < 40 and phase2 < 100 and phase3 < 120:
                battery_output_power = 80 if phase2 < 40 else 100
            else:
                battery_output_power = int((phase1 + phase2) * REACTIVE_POWER_FACTOR)
                phase3_reactive = phase3 * REACTIVE_POWER_FACTOR
                delta = abs(current_output_power - phase3_reactive)
                battery_output_power += delta

            battery_output_power = max(BATTERY_OUTPUT_MIN, min(battery_output_power, BATTERY_OUTPUT_MAX))
    except Exception as e:
        log(f"[Berechnung] Fehler: {e}")

def publish_set_battery_output(mqttc):
    while True:
        try:
            with mqtt_publish_mutex:
                with calc_data_mutex:
                    if battery_output_power != -1:
                        payload = f"cd=20,md=0,a1=1,b1=0:0,e1=23:59,v1={battery_output_power},a2=0,b2=0:0,e2=23:59,v2=80,a3=0,b3=0:0,e3=23:59,v3=80"
                        mqttc.publish("hame_energy/HMA-1/App/2419720d2e06/ctrl", payload)
            time.sleep(20)
        except Exception as e:
            log(f"[Fehler] Batterie-Setzung: {e}")
            time.sleep(10)

def publish_poweroutput_request(mqttc):
    while True:
        try:
            with mqtt_publish_mutex:
                mqttc.publish("powermeter_balkon/command", "status_update")
            time.sleep(1)
        except Exception as e:
            log(f"[Fehler] Leistungsausgabe-Anfrage: {e}")
            time.sleep(5)

def run():
    while True:
        try:
            mqttc = mqtt_init()
            smartmeter = smartmeter_init()
            if not smartmeter:
                log("[Warnung] Smartmeter nicht verfügbar. Wiederhole in 10 Sekunden.")
                time.sleep(10)
                continue

            threads = [
                threading.Thread(target=publish_battery_data_request, args=(mqttc,)),
                threading.Thread(target=smartmeter_receive_data, args=(smartmeter,)),
                threading.Thread(target=publish_smartmeter_values, args=(mqttc,)),
                threading.Thread(target=publish_poweroutput_request, args=(mqttc,)),
                threading.Thread(target=publish_set_battery_output, args=(mqttc,))
            ]

            for t in threads:
                t.daemon = True
                t.start()

            while True:
                time.sleep(60)
        except Exception as e:
            log(f"[Kritischer Fehler] Neustart des Hauptprozesses: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()

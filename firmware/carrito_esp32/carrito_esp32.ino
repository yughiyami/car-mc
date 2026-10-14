// ESP32 + MPU6050 tilt controller. Publishes roll/pitch over MQTT.
//
// Wiring (ESP32 DevKit): MPU6050 VCC->3V3, GND->GND, SDA->GPIO21, SCL->GPIO22
// Library: "PubSubClient" by Nick O'Leary (Library Manager)
//
// Mounting: chip axes X forward, Y left, Z up (as printed on the board).
// If the car steers/accelerates the wrong way, flip SIGN_ROLL / SIGN_PITCH.

#include <WiFi.h>
#include <Wire.h>
#include <PubSubClient.h>

// ---- Configuration ----
const char* WIFI_SSID   = "YOUR_WIFI";
const char* WIFI_PASS   = "YOUR_PASSWORD";
const char* MQTT_HOST   = "192.168.1.50";  // IP of the PC running the broker
const uint16_t MQTT_PORT = 1883;
const char* TOPIC_PREFIX = "carritos";
const char* CAR_NAME    = "";              // empty = derive from the chip MAC

const float SIGN_ROLL  = 1.0f;
const float SIGN_PITCH = 1.0f;
const uint32_t PUBLISH_MS = 40;            // 25 Hz
const float SMOOTHING = 0.3f;              // 0..1, higher = more responsive
// -----------------------

const uint8_t MPU_ADDR = 0x68;

char carId[24];
char topicTilt[64];
char topicStatus[64];

WiFiClient net;
PubSubClient mqtt(net);

float roll = 0, pitch = 0;
uint32_t lastPublish = 0;
uint32_t lastMqttTry = 0;

int16_t read16() {
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  return (int16_t)((hi << 8) | lo);
}

bool readAccel(float& ax, float& ay, float& az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);  // ACCEL_XOUT_H
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)MPU_ADDR, 6) != 6) return false;
  ax = read16() / 16384.0f;  // +-2g range
  ay = read16() / 16384.0f;
  az = read16() / 16384.0f;
  return true;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print('.');
  }
  Serial.printf("\nIP %s\n", WiFi.localIP().toString().c_str());
}

void ensureMqtt() {
  if (mqtt.connected()) return;
  if (millis() - lastMqttTry < 2000) return;
  lastMqttTry = millis();
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    return;
  }
  // Last Will: broker publishes "offline" (retained) if we drop without saying goodbye
  if (mqtt.connect(carId, nullptr, nullptr, topicStatus, 0, true, "offline")) {
    mqtt.publish(topicStatus, "online", true);
    Serial.println("MQTT connected");
  }
}

void setup() {
  Serial.begin(115200);

  if (strlen(CAR_NAME) > 0) {
    snprintf(carId, sizeof(carId), "%s", CAR_NAME);
  } else {
    uint64_t mac = ESP.getEfuseMac();
    snprintf(carId, sizeof(carId), "esp32-%04x", (uint16_t)(mac >> 32));
  }
  snprintf(topicTilt, sizeof(topicTilt), "%s/%s/tilt", TOPIC_PREFIX, carId);
  snprintf(topicStatus, sizeof(topicStatus), "%s/%s/status", TOPIC_PREFIX, carId);
  Serial.printf("Car id: %s\n", carId);

  Wire.begin(21, 22);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);  // PWR_MGMT_1: wake up
  Wire.write(0x00);
  Wire.endTransmission();

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
}

void loop() {
  ensureMqtt();
  mqtt.loop();

  if (!mqtt.connected() || millis() - lastPublish < PUBLISH_MS) return;
  lastPublish = millis();

  float ax, ay, az;
  if (!readAccel(ax, ay, az)) return;

  // Nose down (forward) => positive pitch. Right side down => positive roll.
  float rawRoll  = SIGN_ROLL  * atan2f(ay, az) * 180.0f / PI;
  float rawPitch = SIGN_PITCH * atan2f(-ax, sqrtf(ay * ay + az * az)) * 180.0f / PI;
  roll  += SMOOTHING * (rawRoll  - roll);
  pitch += SMOOTHING * (rawPitch - pitch);

  char payload[64];
  snprintf(payload, sizeof(payload), "{\"roll\":%.1f,\"pitch\":%.1f}", roll, pitch);
  mqtt.publish(topicTilt, payload);
}

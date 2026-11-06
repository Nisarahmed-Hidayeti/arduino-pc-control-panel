#include <SPI.h>
#include <MFRC522.h>

// LOLIN NodeMCU V3 / ESP8266. RC522 power must be 3.3V.
MFRC522 reader(D2, D1); // SS/SDA GPIO4, RST GPIO5

void setup() {
  Serial.begin(115200);
  SPI.begin(); // SCK D5, MISO D6, MOSI D7
  reader.PCD_Init();
  delay(50);
  byte version = reader.PCD_ReadRegister(MFRC522::VersionReg);
  if (version == 0x00 || version == 0xFF) {
    Serial.println("ERROR:RC522_NOT_FOUND");
  } else {
    Serial.println("READY:RFID_PANEL_V1");
  }
}

void loop() {
  if (!reader.PICC_IsNewCardPresent() || !reader.PICC_ReadCardSerial()) {
    delay(20);
    return;
  }
  Serial.print("CARD:");
  for (byte i = 0; i < reader.uid.size; ++i) {
    if (reader.uid.uidByte[i] < 0x10) Serial.print('0');
    Serial.print(reader.uid.uidByte[i], HEX);
  }
  Serial.println();
  reader.PICC_HaltA(); // Card must leave the field before another scan.
  reader.PCD_StopCrypto1();
}

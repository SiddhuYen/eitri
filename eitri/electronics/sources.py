"""Where every number the electronics checks use comes from: the document, its URL, the words it says, and when it was fetched.

A part's numbers name a source id here (Part.srcs); a check names the source in its text ("[source: ...]"). A value the model file
gives is its own source ("model"). A value with no fetchable source is marked ASSUMPTION in its title and in the check that uses it.
Where sources disagree, the conservative figure is used and both are quoted: the lower figure for a limit (a rating, a maximum, a
voltage a part tolerates) and the higher figure for a draw or load (a current a part takes, a stall current, a coil current).

    cite(id)          a short citation for a check's text
    SOURCES[id]       the full record; sources_table() the Markdown table (the reference and every build's sources file)
"""
from __future__ import annotations

from dataclasses import dataclass

FETCHED = "2026-11-05"


@dataclass(frozen=True)
class Source:
    id: str
    short: str                     # what a check's text names
    doc: str                       # the document, revision and date
    url: str
    quote: str                     # what it says (exactly, or the table row)
    fetched: str = FETCHED
    assumption: bool = False


_S = [
    # ---------------------------------------------------------------- microcontrollers and boards
    Source("atmega328p", "ATmega328P datasheet DS40002061B", "Microchip ATmega48A/PA/88A/PA/168A/PA/328/P datasheet DS40002061B, Rev. B 09/2020, "
           "s29.1 Absolute Maximum Ratings and Table 30-1",
           "https://ww1.microchip.com/downloads/aemDocuments/documents/MCU08/ProductDocuments/DataSheets/ATmega48A-PA-88A-PA-168A-PA-328-P-DS-DS40002061B.pdf",
           '"DC Current per I/O Pin 40.0mA"; "DC Current VCC and GND Pins 200.0mA"; "Voltage on any Pin except RESET with respect to Ground -0.5V to '
           'VCC+0.5V"; VIL max 0.3VCC and VIH min 0.6VCC (VCC = 2.4V - 5.5V); "IOL = 20mA, VCC = 5V TA=85C 0.9" (VOL max); "IOH = -20mA, VCC = 5V '
           'TA=85C 4.2" (VOH min); "RPU I/O Pin Pull-up Resistor 20 50 k" (Table 30-1, p.323); s14.2.1 Configuring the Pin (p.85, re-read '
           '2026-11-07): "The port pins are tri-stated when reset condition becomes active, even if no clocks are running."'),
    Source("arduino-uno", "Arduino UNO R3 pinout, tech specs and datasheet rev 4", "Arduino UNO R3 full pinout A000066 (last update 6 Oct 2022), "
           "docs.arduino.cc/hardware/uno-rev3 tech specs, and datasheet A000066 rev 4 (20/10/2026; re-read 2026-11-07)",
           "https://docs.arduino.cc/resources/pinouts/A000066-full-pinout.pdf",
           '"MAXIMUM current per I/O pin is 20mA"; "MAXIMUM current per +3.3V pin is 50mA"; tech specs "Input voltage (nominal): 7-12V", "DC Current per '
           'I/O Pin: 20 mA". Datasheet s2.2: "VINMax Maximum input voltage from VIN pad 6 - 20 V", "VUSBMax Maximum input voltage from USB '
           'connector - 5.5 V"; s3.1 "U1 SPX1117M3-L-5 Regulator". The narrower nominal 7-12 V is used for VIN; 5.5 V is the most the 5 V '
           'rail (USB or the 5V pin, which bypasses the regulator) may see.', fetched="2026-11-07"),
    Source("arduino-nano", "Arduino Nano pinout and tech specs", "Arduino Nano full pinout A000005 (last update 30/06/2021) and docs.arduino.cc/hardware/nano tech specs",
           "https://docs.arduino.cc/resources/pinouts/A000005-full-pinout.pdf",
           '"VIN 7-12 V input to the board."; "MAXIMUM current per I/O pin is 20mA"; "MAXIMUM current per +3.3V pin is 50mA"; tech specs "DC Current per I/O Pin: 20 mA". '
           'The datasheet A000005 rev 5 (20/10/2026, re-read 2026-11-07) says "7-15V unregulated external power supply (pin 30)": the pinout\'s '
           'lower 7-12 V is used.'),
    Source("arduino-uno-sch", "Arduino UNO R3 schematic", "Arduino UNO R3 schematic A000066",
           "https://docs.arduino.cc/resources/schematics/A000066-schematics.pdf",
           'page 1: "U2" "LP2985-33DBVR", pins "1 IN" and "3 ON/OFF" on the net "+5V", "OUT 5 +3V3": the 3.3 V regulator runs from the 5 V rail, so '
           "3V3-pin loads are also 5 V-rail loads (read from the rendered schematic; it gives no current rating, the pinout's 50 mA is used)"),
    Source("esp32", "ESP32 Series Datasheet v5.3", "Espressif ESP32 Series Datasheet v5.3 (2026.07), Tables 5-1, 5-2, 5-3",
           "https://documentation.espressif.com/esp32_datasheet_en.pdf",
           '"VIH High-level input voltage 0.75 x VDD - VDD + 0.3 V"; "VIL ... 0.25 x VDD"; "VOH ... 0.8 x VDD"; "VOL ... 0.1 x VDD"; high-level source '
           'current 40 mA typ (VDD3P3_CPU / _RTC); "Low-level sink current (VDD = 3.3 V, VOL = 0.495 V ...) 28 mA" typ; "Cumulative IO output current 1200 mA"; '
           '"Current delivered by external power supply 0.5 A" (min). The lower per-pin figure, 28 mA, is used for both directions.'),
    Source("esp32-devkitc", "ESP32-DevKitC V4 schematic", "Espressif ESP32-DevKitC V4 schematic (25 Feb 2020) and user guide",
           "https://dl.espressif.com/dl/schematics/esp32_devkitc_v4_sch.pdf",
           '"U2 AMS1117-3.3" feeds the 3V3 rail from USB 5 V; neither the guide nor the schematic gives a current for external loads. The limit used '
           "is the AMS1117's rating less the 0.5 A the ESP32 datasheet asks its supply to deliver."),
    # ---------------------------------------------------------------- I2C and modules
    Source("arduino-wire-avr", "Arduino AVR core, Wire library (twi.c)", "arduino/ArduinoCore-avr, libraries/Wire/src/utility/twi.c (master; last changed "
           "26 May 2021, commit 8b327d7). LGPL-2.1: read for its behaviour only, nothing copied",
           "https://github.com/arduino/ArduinoCore-avr/blob/master/libraries/Wire/src/utility/twi.c",
           "twi_init(), which Wire.begin() calls, writes SDA and SCL high, which turns on the ATmega's internal pull-ups on both lines (its comment: "
           '"activate internal pullups for twi."); twi_disable() (Wire.end()) writes them low again. The pull-ups are 20-50 kOhm to VCC (5 V on an '
           "Uno or Nano): see the ATmega328P datasheet, Table 30-1, RPU."),
    Source("nxp-um10204", "NXP UM10204 Rev. 7.0", "NXP UM10204 I2C-bus specification and user manual, Rev. 7.0, 1 October 2021: Table 10, Table 11, s7.1",
           "https://www.nxp.com/docs/en/user-guide/UM10204.pdf",
           'VOL1 max 0.4 V "at 3 mA sink current; VDD > 2 V"; IOL 3 mA (Standard-, Fast-mode), 20 mA (Fast-mode Plus); VIL max 0.3VDD, VIH min 0.7VDD; '
           '"tr rise time of both SDA and SCL signals - 1000 | 20 300 | - 120 ns"; "Cb capacitive load for each bus line - 400 | - 400 | - 550 pF"; '
           'Ci max 10 pF; s7.1 "Rp(max) = tr / (0.8473 x Cb)", "Rp(min) = (VDD - VOL(max)) / IOL"'),
    Source("nxp-pca9685", "NXP PCA9685 datasheet Rev. 4", "NXP PCA9685 product data sheet, Rev. 4, 16 April 2015",
           "https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf",
           '"VDD supply voltage 2.3 - 5.5 V"; "the inputs and outputs are 5.5 V tolerant"; VIL max 0.3VDD, VIH min 0.7VDD; "IO(LEDn) output current on pin '
           'LEDn - 25 mA"; "operating mode; no load; fSCL = 1 MHz - 6 10 mA" (IDD); address "1 A5 A4 A3 A2 A1 A0"; LED outputs "totem pole with a 25 mA sink, 10 mA source capability at 5 V", VOH min '
           '1.6 V at VDD 2.3 V, 2.3 V at 3.0 V, 4.0 V at 4.5 V (IOH = -10 mA): 0.69 x VDD, the lowest ratio, is used'),
    Source("adafruit-pca9685", "Adafruit PCA9685 breakout (product 815)", "Adafruit 16-Channel 12-bit PWM/Servo Driver: learn pages (pinouts, last edited "
           "May 19 2026; hooking it up) and product page 815",
           "https://learn.adafruit.com/16-channel-pwm-servo-driver/pinouts",
           '"VCC - This is the logic power pin, ... should be 3 - 5V max! It\'s also used for the 10K pullups on SCL/SDA"; "V+ ... It is not used at all by the '
           'chip"; "The default I2C address is 0x40."; "There are 220 ohm resistors in series with all PWM Pins". V+: "5-6VDC if you are using servos" '
           '(pinouts page; up to 12 VDC "if you have to") vs "The V+ pin can be as high as 6V" (hooking-it-up page): the lower, 6 V, is used.'),
    Source("bosch-bme280", "Bosch BME280 datasheet 1.24", "Bosch Sensortec BME280 datasheet BST-BME280-DS001-24, rev 1.24 (Feb 2024; page footers say 1.23)",
           "https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf",
           '"Connecting SDO to GND results in slave address 1110110 (0x76); connection it to VDDIO results in slave address 1110111 (0x77)"; VDD 1.71-3.6 V; '
           '"Voltage at any supply pin VDD and VDDIO pin -0.3 4.25 V"; "Voltage at any interface pin -0.3 VDDIO + 0.3 V"; VIL max 20% VDDIO, VIH min 80% '
           'VDDIO; "IDDP Max value at -40 C 714 uA"'),
    Source("ssd1306", "SSD1306 datasheet Rev 1.1 / Adafruit 326", "Solomon Systech SSD1306 Rev 1.1 (Apr 2008, Adafruit copy) and Adafruit 0.96\" OLED "
           "product 326 / learn page (last edited March 08 2024)",
           "https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf",
           '"Either 0111100 or 0111101, can be selected as the slave address"; Adafruit 326: "5V-ready, with an onboard regulator", "Vin to a 3 to 5V power '
           'supply", "They tend to draw ~20mA or so in practice"; the module schematic shows level shifters and 10K pull-ups (the value read from a '
           'low-resolution image)'),
    Source("hc-sr04", "HC-SR04 datasheet (ElecFreaks)", "ElecFreaks HC-SR04 ultrasonic ranging module datasheet (SparkFun copy, 2011)",
           "https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf",
           '"Working Voltage DC 5 V"; "Working Current 15mA"; "Trigger Input Signal 10uS TTL pulse"; "Echo Output Signal Input TTL lever signal". No '
           "voltage is given for Echo's high level beyond TTL at a 5 V supply."),
    Source("towerpro-mg996r", "TowerPro MG996R", "TowerPro MG996R product page (towerpro.com.tw) and Electronicos Caldas MG996R datasheet (2014-08-19)",
           "https://towerpro.com.tw/product/mg996r/",
           'TowerPro: "Operating voltage: 4.8~ 6.6v", "Stall current draw 1400mA" (no voltage stated). The distributor datasheet says "Stall Current 2.5 A '
           '(6V)" and "Operating voltage: 4.8 V a 7.2 V". By the conservative-figure rule the higher draw, 2.5 A, and the narrower range, TowerPro\'s '
           "4.8-6.6 V, are used."),
    Source("towerpro-sg90", "TowerPro SG90 (Q&A reply for current)", "TowerPro SG90 product page (towerpro.com.tw), spec list and Q&A",
           "https://towerpro.com.tw/product/sg90-7/",
           '"Operating voltage: 4.8v"; "Stall torque: 1.8kg/cm(4.8v)"; the spec list publishes no current. The only figure is an admin reply '
           'in the page\'s Q&A (2021-11-07, re-read 2026-11-07): "The operation current for our SG90 is about 0.5A - 2A. Voltages from 4.8V - 6V '
           'are fine." By the conservative-figure rule (the higher figure for a draw) 2 A is used as its stall draw (0.5 A up to eitri 0.7.0). '
           "ASSUMPTION: that the reply's upper figure is the stall current; give servo(..., current_A=) from a measured stall current.",
           fetched="2026-11-07", assumption=True),
    # ---------------------------------------------------------------- batteries (c.battery)
    Source("molicel-p26a", "Molicel INR-18650-P26A data sheet v1.4", "Molicel INR-18650-P26A Product Data Sheet, version 1.4 (lithium-ion)",
           "https://www.molicel.com/wp-content/uploads/INR18650P26A_1.4_Product-Data-Sheet-of-INR-18650-P26A-80087.pdf",
           'Cell Voltage: "Nominal 3.6 V", "Charge 4.2 V", "Discharge 2.5 V"; Charge Characteristics: "CC-CV Charge to 4.2V". The 4.2 V charge '
           "voltage is the lithium-ion (and lithium-polymer) full-charge figure used per cell.", fetched="2026-11-07"),
    Source("yuasa-np7-12", "Yuasa NP7-12 data sheet", "Yuasa NP7-12 Industrial VRLA Battery, technical data sheet (yuasaeurope.com, French edition)",
           "https://res.cloudinary.com/sonepar-fr/raw/upload/s--wpLzFKbh--/v1/documents/894/NP7-12.pdf",
           '"Tension nominale 12" V; "Tension de charge en floating a 20C (V)/bloc 13.65 (+-1%)"; "Tension de charge en cyclique (ou Boost) a '
           '20C (V)/bloc 14.5 (+-3%)", 2.42 V per cell. A 12 V lead-acid battery on charge (a charger, a vehicle) reaches 14.5 V: the full-charge '
           "figure used.", fetched="2026-11-07"),
    # ---------------------------------------------------------------- passives and power parts (filled from the fetched datasheets below)
    Source("iec-60063", "IEC 60063 E-series (Vishay 28372)", "IEC 60063 (a paid standard, not fetched); its values from Vishay \"E-Series Values ... "
           "according to IEC 60063\", document 28372, rev 22-Aug-12", "https://www.vishay.com/docs/28372/e-series.pdf",
           "E24: 10 11 12 13 15 16 18 20 22 24 27 30 33 36 39 43 47 51 56 62 68 75 82 91; E12: 10 12 15 18 22 27 33 39 47 56 68 82"),
    Source("yageo-cfr", "Yageo CFR datasheet V.3", "Yageo CFR carbon film resistors, product specification V.3, April 3 2024 (MFR V.4 says the same for MFR-25)",
           "https://yageogroup.com/content/Resource%20Library/Datasheet/YAGEO-CFR_DATASHEET.pdf",
           'Table 1 "Power Rating at 70 C ... 1/4W" (CFR-25); derating curve flat to 70 C, then to 0 % at 155 C. The default for a resistor given '
           "no power_W: a 1/4 W through-hole part, at an ambient under 70 C."),
    Source("kingbright-red", "Kingbright WP7113SRD/D datasheet", "Kingbright WP7113SRD/D 5 mm super bright red, DSAF2433 Rev V.9A (08/05/2020)",
           "https://www.kingbrightusa.com/images/catalog/SPEC/WP7113SRD-D.pdf",
           '"Forward Voltage IF = 20mA VF Super Bright Red 1.85 2.5 V" (typ, max); "DC Forward Current IF 30 mA". (WP7113ID gives 1.9 / 2.3 V at 10 mA, '
           "30 mA DC.)"),
    Source("kingbright-green", "Kingbright WP7113SGD datasheet", "Kingbright WP7113SGD 5 mm super bright green, DSAF2427 Rev V.11B (01/09/2021)",
           "https://www.kingbrightusa.com/images/catalog/SPEC/WP7113SGD.pdf",
           "VF 2.2 typ / 2.5 max V at IF = 20 mA; DC forward current 25 mA. (WP7113CGCK: 2.1 V, 30 mA; WP7113GD: 25 mA; the lower 25 mA is used.)"),
    Source("kingbright-blue", "Kingbright WP7113QBC/D datasheet", "Kingbright WP7113QBC/D 5 mm blue, DSAG3481 Rev V.18B (03/26/2025)",
           "https://www.kingbrightusa.com/images/catalog/SPEC/WP7113QBC-D.pdf",
           '"Forward Voltage IF = 20mA VF Blue 3.3 4.0 V"; DC forward current 30 mA'),
    Source("kingbright-white", "Kingbright WP7113QWC/D datasheet", "Kingbright WP7113QWC/D 5 mm white, DSAI6407 Rev V.6B (10/09/2023)",
           "https://www.kingbrightusa.com/images/catalog/SPEC/WP7113QWC-D.pdf", "VF 3.3 typ / 4.0 max V at IF = 20 mA; DC forward current 30 mA"),
    Source("ams1117", "AMS1117 datasheet (2009)", "Advanced Monolithic Systems AMS1117 datasheet (no revision; 27 Aug 2009)",
           "http://www.advanced-monolithic.com/pdf/ds1117.pdf",
           '"Output Current of 1A"; "Dropout Voltage ... IOUT = 0.8A (Note 4) 1.1 1.3 V"; "Note 4: Dropout voltage is specified up to 0.8A load. For '
           'currents over 0.8A dropout will be higher"; "SOT-223 package phiJA = 90 C/W ... can vary from 46 C/W to >90 C/W depending on mounting"; '
           '"Operating Junction Temperature Range ... -40C to 125 C"; "Input Voltage 15V"; "Quiescent Current AMS1117-1.5/.../-3.3/-5.0 (VIN - VOUT) = 1.5V 5 11 mA" '
           '(the 11 mA maximum is used). The 90 C/W figure (no copper area assumed) is used.'),
    Source("st-l7805", "ST L78 datasheet Rev 34 (ST-authored copy)", "ST L78 series datasheet DocID2143 Rev 34 (Nov 2016), an unmodified ST-authored "
           "copy on protostack.com.au. Re-checked 2026-11-05: st.com (www.st.com/resource/en/datasheet/l78.pdf) timed out again, Mouser's copy "
           "returned a bot-check page, DigiKey's datasheet page answered 403, and the copy Octopart serves is Rev 13 (2006), older than this one. "
           "The current revision on st.com (said to be Rev 38) is unverified: a manual refresh is pending",
           "https://protostack.com.au/download/l7805.pdf",
           '"Output current up to 1.5 A"; Table 10 (L7805C) "Vd Dropout voltage IO = 1 A, TJ = 25 C 2 V" (typical: no maximum is given) and '
           '"Id Quiescent current TJ = 25 C 8 mA" (max); "RthJA ... 50" C/W for TO-220; "Operating junction temperature range for L78xxC, L78xxAC 0 to '
           '125 C"; "VI DC input voltage for VO= 5 to 18 V 35 V"'),
    Source("songle-srd", "Songle SRD(T73) datasheet V1", "Songle SRD(T73) series relay datasheet, Version V1 (undated, songlerelay.com)",
           "https://www.songlerelay.com/upload/8670/srd-t73-relay-290486.pdf",
           '"Power consumption(W) 0.36W(L) ... Nominal voltage(VDC) 3 5 6 ... Coil resistance(Ohm+-10%) 25 69.4 100"; "Contact rating(Res. load) 7A/10A/15A '
           '250VAC/28VDC". An older Songle sheet says 70 Ohm / 71.4 mA; the lower resistance (the higher current), 69.4 Ohm, is used.'),
    Source("diode-1n4148", "1N4148 (onsemi Rev 6, Vishay Rev 1.6)", "onsemi 1N914/D Rev. 6 (Sept 2024) and Vishay 81857 Rev. 1.6 (07-Nov-2024)",
           "https://www.vishay.com/docs/81857/1n4148.pdf",
           'onsemi "Average Rectified Forward Current IO 200 mA"; Vishay "Average forward current VR = 0 IF(AV) 150 mA"; both IF 300 mA DC, VRRM 100 V. '
           'The lower 150 mA is used. VF: "IF = 10 mA 1.0 V" (max; no typical given)'),
    Source("diode-1n4007", "1N4007 (onsemi Rev 18, Vishay 2020)", "onsemi 1N4001/D Rev. 18 (June 2024) and Vishay 88503 (29-Apr-2020)",
           "https://www.vishay.com/docs/88503/1n4001.pdf", '"Maximum average forward rectified current ... IF(AV) 1.0 A"; VRRM 1000 V (1N4007); onsemi "Maximum Instantaneous Forward Voltage '
           'Drop, (iF = 1.0 Amp, TJ = 25C) vF 0.93 1.1 V" (typ 0.93 is used: the lower forward voltage gives the higher string current)'),
    Source("diode-1n5819", "1N5819 (onsemi Rev 11, Vishay 2020)", "onsemi 1N5817/D Rev. 11 (Aug 2021) and Vishay 88525 (01-Jul-2020)",
           "https://www.vishay.com/docs/88525/1n5817.pdf", '"IF(AV) 1.0 A"; VRRM 40 V (1N5819); VF max 0.34 V at 0.1 A, 0.6 V at 1.0 A (onsemi, pulse test)'),
    Source("te-coil-suppression", "TE application note 13C3264", "TE / Tyco P&B application note 13C3264 \"Coil Suppression Can Reduce Relay Life\" (12-00)",
           "https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm=13C3264_AppNote&DocType=CS&DocLang=EN",
           '"As the electrical current in a relay coil is interrupted, an induced voltage transient of the order of hundreds or even thousands of volts may '
           'be generated across that coil"; "simply shunt the coil with a general purpose diode ... This diode shunt provides maximum protection to the '
           'solid state switch" (it slows the relay\'s release; a diode plus a Zener is the note\'s compromise)'),
    Source("infineon-irlz44n", "Infineon IRLZ44NPbF datasheet", "International Rectifier / Infineon IRLZ44NPbF, PD-94831 (11/11/03)",
           "https://www.infineon.com/assets/row/public/documents/24/49/infineon-irlz44n-datasheet-en.pdf",
           'RDS(on) max "0.022 VGS = 10V, ID = 25A", "0.025 Ohm VGS = 5.0V, ID = 25A", "0.035 VGS = 4.0V, ID = 21A"; VGS(th) 1.0-2.0 V; ID 47 A '
           '(TC = 25 C); "RthetaJA Junction-to-Ambient 62"; TJ -55 to +175 C'),
    Source("infineon-irf540n", "Infineon IRF540NPbF datasheet Rev 2.1", "Infineon IRF540NPbF Rev. 2.1 (2026-07-24)",
           "https://www.infineon.com/assets/row/public/documents/24/49/infineon-irf540n-datasheet-en.pdf",
           '"RDS(on) ... 44 mOhm VGS = 10V, ID = 16A" (no RDS(on) at a lower VGS); VGS(th) 2.0-4.0 V; ID 33 A; RthetaJA 62 C/W; TJ -55 to +175 C'),
    Source("onsemi-2222a", "onsemi P2N2222A / PN2222A", "onsemi P2N2222A/D Rev. 7 (Jan 2013) and PN2222A Rev. 1.1.0 (July 2014)",
           "https://www.onsemi.com/download/data-sheet/pdf/p2n2222a-d.pdf",
           'P2N2222A "Collector Current - Continuous IC 600 mAdc" (PN2222A: 1.0 A; the lower 600 mA is used); VCE(sat) "IC = 150 mA, IB = 15 mA 0.3 V; '
           'IC = 500 mA, IB = 50 mA 1.0" (the datasheet\'s saturation condition IC/IB = 10); "VBE(sat) ... IC = 150 mA, IB = 15 mA 0.6 1.2 V" '
           '(min, max: 0.6 V for the most base current a pin gives, 1.2 V for the least base drive)'),
    Source("hc-sr04-range", "HC-SR04 supply range (ASSUMPTION)", "ASSUMPTION: the HC-SR04 datasheet gives \"Working Voltage DC 5 V\" and no range; 4.5-5.5 V "
           "(+-10 %) is assumed", "https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf", "Working Voltage DC 5 V (no tolerance given)", assumption=True),
]

SOURCES: dict[str, Source] = {s.id: s for s in _S}


def add(src: Source) -> None:
    SOURCES[src.id] = src


def cite(sid: str) -> str:
    if not sid:
        return ""
    if sid == "model":
        return "the model file"
    s = SOURCES.get(sid)
    if s is None:
        return sid
    return s.short + (" (ASSUMPTION)" if s.assumption and "ASSUMPTION" not in s.short else "") + f", fetched {s.fetched}"


def sources_table(ids=None) -> str:
    rows = ["| id | document | URL | what it says | fetched |", "|---|---|---|---|---|"]
    esc = lambda x: str(x).replace("|", "\\|")
    for s in SOURCES.values():
        if ids is not None and s.id not in ids:
            continue
        rows.append(f"| {s.id}{' (ASSUMPTION)' if s.assumption else ''} | {esc(s.doc)} | {s.url} | {esc(s.quote)} | {s.fetched} |")
    return "\n".join(rows)

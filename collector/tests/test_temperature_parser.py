import unittest

from temperature_parser import extract_temperature_records, normalize_time


class TemperatureParserTests(unittest.TestCase):
    def test_mixed_lux_and_temperature_probes(self):
        payload = {
            "code": 0,
            "data": [
                {
                    "probeName": "探头1",
                    "unitName": "勒克斯",
                    "unitCode": "Lux",
                    "probeDataList": [
                        {
                            "monitorTime": "2026-08-18 10:00:00(+08:00)",
                            "value": "206.7",
                            "unitCode": "Lux",
                        }
                    ],
                },
                {
                    "probeName": "探头2",
                    "unitName": "摄氏度",
                    "unitCode": "℃",
                    "probeDataList": [
                        {
                            "monitorTime": "2026-08-18 10:00:00(+08:00)",
                            "value": "27.6",
                            "unitCode": "℃",
                        }
                    ],
                },
            ],
        }
        self.assertEqual(
            extract_temperature_records(payload),
            [{"time": "2026-08-18 10:00:00", "temperature": 27.6}],
        )

    def test_generic_value_without_temperature_context_is_ignored(self):
        payload = {
            "data": [
                {"monitorTime": "2026-08-18 10:00:00", "value": "145.8"}
            ]
        }
        self.assertEqual(extract_temperature_records(payload), [])

    def test_allowed_probe_name_supplies_context(self):
        payload = {
            "probeName": "探头2",
            "probeDataList": [
                {"monitorTime": 1787028000, "value": "-3.5"}
            ],
        }
        records = extract_temperature_records(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["temperature"], -3.5)

    def test_timezone_suffix_is_normalized(self):
        self.assertEqual(
            normalize_time("2026-08-18 10:00:00(+08:00)"),
            "2026-08-18 10:00:00",
        )


if __name__ == "__main__":
    unittest.main()



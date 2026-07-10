from __future__ import annotations

import unittest

from eventlog_helpdesk.eventlog_backend import parse_event_xml
from eventlog_helpdesk.markdown_exporter import events_to_markdown


SAMPLE_XML = """<Event xmlns=\"http://schemas.microsoft.com/win/2004/08/events/event\">
<System>
  <Provider Name=\"Example-Provider\" Guid=\"{00000000-0000-0000-0000-000000000000}\"/>
  <EventID Qualifiers=\"0\">1001</EventID>
  <Version>2</Version><Level>2</Level><Task>7</Task><Opcode>0</Opcode><Keywords>0x80000000000000</Keywords>
  <TimeCreated SystemTime=\"2026-07-10T12:34:56.0000000Z\"/>
  <EventRecordID>42</EventRecordID>
  <Correlation ActivityID=\"{11111111-1111-1111-1111-111111111111}\"/>
  <Execution ProcessID=\"1234\" ThreadID=\"88\"/>
  <Channel>System</Channel><Computer>TEST-PC</Computer><Security UserID=\"S-1-5-18\"/>
</System>
<EventData>
  <Data Name=\"Status\">0xC0000001</Data>
  <Data Name=\"Path\">C:\\Windows\\Example.dll</Data>
</EventData>
<RenderingInfo Culture=\"en-US\">
  <Message>An example service failed.</Message><Level>Error</Level><Task>Service Control</Task><Opcode>Info</Opcode>
</RenderingInfo>
</Event>"""


class EventParserTests(unittest.TestCase):
    def test_parses_core_fields_and_preserves_xml(self) -> None:
        event = parse_event_xml(SAMPLE_XML, "System")
        self.assertEqual(event.provider, "Example-Provider")
        self.assertEqual(event.event_id, "1001")
        self.assertEqual(event.level_value, 2)
        self.assertEqual(event.level, "Error")
        self.assertEqual(event.record_id, "42")
        self.assertEqual(event.computer, "TEST-PC")
        self.assertIn("service failed", event.message)
        self.assertEqual(event.raw_xml, SAMPLE_XML)
        fields = dict(event.fields)
        self.assertEqual(fields["Event/System/Provider/@Name"], "Example-Provider")
        self.assertEqual(fields["Event/EventData/Data[1]/@Name"], "Status")
        self.assertEqual(fields["Event/EventData/Data[2]"], r"C:\Windows\Example.dll")

    def test_complete_markdown_contains_raw_xml_and_all_fields(self) -> None:
        event = parse_event_xml(SAMPLE_XML, "System")
        markdown = events_to_markdown([event], title="Test", source="System")
        self.assertIn("Complete XML field map", markdown)
        self.assertIn("Raw event XML", markdown)
        self.assertIn("Example-Provider", markdown)
        self.assertIn("Status", markdown)
        self.assertIn(SAMPLE_XML, markdown)


if __name__ == "__main__":
    unittest.main()

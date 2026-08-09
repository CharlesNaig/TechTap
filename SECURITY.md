# Security policy

Please report suspected security issues privately through GitHub's private
vulnerability reporting feature when available, or contact the maintainer through the
profile linked in this repository. Do not publish working exploits, device identifiers,
Wi-Fi credentials, NFC payloads containing personal data, or database exports.

TOMOTAP runs a local phone bridge and can write persistent tag data. Review setup
scripts before executing them, approve only devices you recognize in ADB, keep the
bridge bound to local interfaces, and verify a tag's payload before sharing or locking
it. Tag locking can be permanent.

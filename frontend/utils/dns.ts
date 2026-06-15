/**
 * DNS utility functions for type/class conversion and examples.
 */

// Common DNS types for the dropdown
export const COMMON_TYPES = [
  'A',
  'AAAA',
  'CAA',
  'CNAME',
  'DNSKEY',
  'DS',
  'HTTPS',
  'LOC',
  'MX',
  'NAPTR',
  'NS',
  'PTR',
  'SOA',
  'SPF',
  'SRV',
  'SSHFP',
  'SVCB',
  'TLSA',
  'TXT',
];

// Common DNS classes for the dropdown
export const COMMON_CLASSES = ['IN', 'CH', 'HS'];

// Type number to name mapping
const TYPE_MAP: Record<number, string> = {
  1: 'A',
  2: 'NS',
  5: 'CNAME',
  6: 'SOA',
  12: 'PTR',
  15: 'MX',
  16: 'TXT',
  28: 'AAAA',
  29: 'LOC',
  33: 'SRV',
  35: 'NAPTR',
  43: 'DS',
  44: 'SSHFP',
  46: 'RRSIG',
  47: 'NSEC',
  48: 'DNSKEY',
  50: 'NSEC3',
  51: 'NSEC3PARAM',
  52: 'TLSA',
  59: 'CDS',
  60: 'CDNSKEY',
  64: 'SVCB',
  65: 'HTTPS',
  99: 'SPF',
  257: 'CAA',
};

// Class number to name mapping
const CLASS_MAP: Record<number, string> = {
  1: 'IN',
  3: 'CH',
  4: 'HS',
  254: 'NONE',
  255: 'ANY',
};

// Record type examples
const RECORD_EXAMPLES: Record<string, string[]> = {
  A: ['192.0.2.1', '198.51.100.25'],
  AAAA: ['2001:db8::1', '2001:db8:85a3::8a2e:370:7334'],
  CNAME: ['www.example.com.', 'alias.example.net.'],
  MX: ['10 mail.example.com.', '20 backup.example.com.'],
  TXT: [
    '"v=spf1 include:_spf.example.com ~all"',
    '"google-site-verification=abc123"',
  ],
  NS: ['ns1.example.com.', 'ns2.example.net.'],
  PTR: ['host.example.com.', 'server.example.net.'],
  SRV: ['10 5 5060 sip.example.com.', '20 0 443 server.example.com.'],
  CAA: ['0 issue "letsencrypt.org"', '0 issuewild ";"'],
  SSHFP: ['1 1 abc123def456...', '4 2 abc123def456...'],
  TLSA: ['3 1 1 abc123def456...', '2 0 1 abc123def456...'],
  DNSKEY: ['257 3 13 base64key...', '256 3 13 base64key...'],
  DS: ['12345 13 2 abc123def456...', '12345 8 2 abc123def456...'],
  NAPTR: [
    '100 10 "u" "sip+E2U" "!^.*$!sip:info@example.com!" .',
    '100 10 "s" "http+N2L+N2C+N2R" "" www.example.com.',
  ],
  LOC: ['37 46 30.000 N 122 25 10.000 W 10m', '51 30 12.748 N 0 7 39.611 W 0m'],
  SPF: ['"v=spf1 mx -all"', '"v=spf1 ip4:192.0.2.0/24 -all"'],
  HTTPS: ['1 . alpn="h2,h3"', '1 example.com. alpn="h2" ipv4hint="192.0.2.1"'],
  SVCB: ['1 . alpn="h2"', '0 svc.example.com.'],
  SOA: ['ns1.example.com. admin.example.com. 2024010101 3600 900 604800 86400'],
};

/**
 * Convert type number to name (common types only).
 */
export function typeNumberToName(num: number): string | null {
  return TYPE_MAP[num] || null;
}

/**
 * Get example values for a record type.
 */
export function getRecordExamples(type: string | undefined): string[] {
  if (!type) return [];
  return RECORD_EXAMPLES[type.toUpperCase()] || [];
}

/**
 * Get the final type value for API submission.
 * Handles hex (0x...) and decimal numbers.
 */
export function getFinalRecordType(typeInput: string): string {
  const type = typeInput.trim().toUpperCase();

  // Convert hex to decimal TYPE format
  if (type.startsWith('0X')) {
    const num = Number.parseInt(type, 16);
    if (!Number.isNaN(num)) {
      return typeNumberToName(num) || `TYPE${num}`;
    }
  }
  // Convert pure decimal to TYPE format or name
  else if (/^\d+$/.test(type)) {
    const num = Number.parseInt(type, 10);
    if (!Number.isNaN(num)) {
      return typeNumberToName(num) || `TYPE${num}`;
    }
  }

  return type;
}

/**
 * Get the final class value for API submission.
 * Handles hex (0x...) and decimal numbers.
 */
export function getFinalRecordClass(classInput: string): string {
  const rdclass = classInput.trim().toUpperCase();

  // Convert hex to decimal CLASS format
  if (rdclass.startsWith('0X')) {
    const num = Number.parseInt(rdclass, 16);
    if (!Number.isNaN(num)) {
      return CLASS_MAP[num] || `CLASS${num}`;
    }
  }
  // Convert pure decimal to CLASS format or name
  else if (/^\d+$/.test(rdclass)) {
    const num = Number.parseInt(rdclass, 10);
    if (!Number.isNaN(num)) {
      return CLASS_MAP[num] || `CLASS${num}`;
    }
  }

  return rdclass || 'IN';
}

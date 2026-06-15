import { describe, expect, it } from 'vitest';
import {
  COMMON_CLASSES,
  COMMON_TYPES,
  getFinalRecordClass,
  getFinalRecordType,
  getRecordExamples,
  typeNumberToName,
} from '../../utils/dns';

describe('COMMON_TYPES', () => {
  it('should include essential DNS record types', () => {
    expect(COMMON_TYPES).toContain('A');
    expect(COMMON_TYPES).toContain('AAAA');
    expect(COMMON_TYPES).toContain('CNAME');
    expect(COMMON_TYPES).toContain('MX');
    expect(COMMON_TYPES).toContain('TXT');
    expect(COMMON_TYPES).toContain('NS');
    expect(COMMON_TYPES).toContain('SOA');
    expect(COMMON_TYPES).toContain('PTR');
  });

  it('should include modern record types', () => {
    expect(COMMON_TYPES).toContain('HTTPS');
    expect(COMMON_TYPES).toContain('SVCB');
    expect(COMMON_TYPES).toContain('CAA');
    expect(COMMON_TYPES).toContain('TLSA');
  });
});

describe('COMMON_CLASSES', () => {
  it('should include standard DNS classes', () => {
    expect(COMMON_CLASSES).toContain('IN');
    expect(COMMON_CLASSES).toContain('CH');
    expect(COMMON_CLASSES).toContain('HS');
  });

  it('should have IN as the first (default) class', () => {
    expect(COMMON_CLASSES[0]).toBe('IN');
  });
});

describe('typeNumberToName', () => {
  it('should convert common type numbers to names', () => {
    expect(typeNumberToName(1)).toBe('A');
    expect(typeNumberToName(28)).toBe('AAAA');
    expect(typeNumberToName(5)).toBe('CNAME');
    expect(typeNumberToName(15)).toBe('MX');
    expect(typeNumberToName(16)).toBe('TXT');
    expect(typeNumberToName(2)).toBe('NS');
    expect(typeNumberToName(6)).toBe('SOA');
    expect(typeNumberToName(12)).toBe('PTR');
  });

  it('should convert DNSSEC type numbers', () => {
    expect(typeNumberToName(43)).toBe('DS');
    expect(typeNumberToName(46)).toBe('RRSIG');
    expect(typeNumberToName(47)).toBe('NSEC');
    expect(typeNumberToName(48)).toBe('DNSKEY');
  });

  it('should convert modern type numbers', () => {
    expect(typeNumberToName(65)).toBe('HTTPS');
    expect(typeNumberToName(64)).toBe('SVCB');
    expect(typeNumberToName(257)).toBe('CAA');
  });

  it('should return null for unknown type numbers', () => {
    expect(typeNumberToName(9999)).toBeNull();
    expect(typeNumberToName(0)).toBeNull();
    expect(typeNumberToName(-1)).toBeNull();
  });
});

describe('getRecordExamples', () => {
  it('should return examples for A records', () => {
    const examples = getRecordExamples('A');
    expect(examples.length).toBeGreaterThan(0);
    expect(examples[0]).toMatch(/^\d+\.\d+\.\d+\.\d+$/);
  });

  it('should return examples for AAAA records', () => {
    const examples = getRecordExamples('AAAA');
    expect(examples.length).toBeGreaterThan(0);
    expect(examples[0]).toContain(':');
  });

  it('should return examples for MX records', () => {
    const examples = getRecordExamples('MX');
    expect(examples.length).toBeGreaterThan(0);
    expect(examples[0]).toMatch(/^\d+\s+\S+\.$/);
  });

  it('should return examples for TXT records', () => {
    const examples = getRecordExamples('TXT');
    expect(examples.length).toBeGreaterThan(0);
    expect(examples[0]).toContain('"');
  });

  it('should be case-insensitive', () => {
    expect(getRecordExamples('a')).toEqual(getRecordExamples('A'));
    expect(getRecordExamples('aaaa')).toEqual(getRecordExamples('AAAA'));
    expect(getRecordExamples('Mx')).toEqual(getRecordExamples('MX'));
  });

  it('should return empty array for unknown types', () => {
    expect(getRecordExamples('UNKNOWN')).toEqual([]);
    expect(getRecordExamples('FAKE')).toEqual([]);
  });

  it('should return empty array for undefined', () => {
    expect(getRecordExamples(undefined)).toEqual([]);
  });
});

describe('getFinalRecordType', () => {
  it('should pass through standard type names', () => {
    expect(getFinalRecordType('A')).toBe('A');
    expect(getFinalRecordType('AAAA')).toBe('AAAA');
    expect(getFinalRecordType('CNAME')).toBe('CNAME');
  });

  it('should uppercase type names', () => {
    expect(getFinalRecordType('a')).toBe('A');
    expect(getFinalRecordType('aaaa')).toBe('AAAA');
    expect(getFinalRecordType('cname')).toBe('CNAME');
  });

  it('should trim whitespace', () => {
    expect(getFinalRecordType('  A  ')).toBe('A');
    expect(getFinalRecordType('\tAAAA\n')).toBe('AAAA');
  });

  it('should convert decimal numbers to type names', () => {
    expect(getFinalRecordType('1')).toBe('A');
    expect(getFinalRecordType('28')).toBe('AAAA');
    expect(getFinalRecordType('15')).toBe('MX');
  });

  it('should convert unknown decimal numbers to TYPE format', () => {
    expect(getFinalRecordType('9999')).toBe('TYPE9999');
    expect(getFinalRecordType('300')).toBe('TYPE300');
  });

  it('should convert hex numbers to type names', () => {
    expect(getFinalRecordType('0x1')).toBe('A');
    expect(getFinalRecordType('0x1c')).toBe('AAAA');
    expect(getFinalRecordType('0X0F')).toBe('MX');
  });

  it('should convert unknown hex numbers to TYPE format', () => {
    expect(getFinalRecordType('0xFFFF')).toBe('TYPE65535');
  });
});

describe('getFinalRecordClass', () => {
  it('should pass through standard class names', () => {
    expect(getFinalRecordClass('IN')).toBe('IN');
    expect(getFinalRecordClass('CH')).toBe('CH');
    expect(getFinalRecordClass('HS')).toBe('HS');
  });

  it('should uppercase class names', () => {
    expect(getFinalRecordClass('in')).toBe('IN');
    expect(getFinalRecordClass('ch')).toBe('CH');
  });

  it('should trim whitespace', () => {
    expect(getFinalRecordClass('  IN  ')).toBe('IN');
  });

  it('should default to IN for empty string', () => {
    expect(getFinalRecordClass('')).toBe('IN');
  });

  it('should convert decimal numbers to class names', () => {
    expect(getFinalRecordClass('1')).toBe('IN');
    expect(getFinalRecordClass('3')).toBe('CH');
    expect(getFinalRecordClass('4')).toBe('HS');
  });

  it('should convert unknown decimal numbers to CLASS format', () => {
    expect(getFinalRecordClass('9999')).toBe('CLASS9999');
  });

  it('should convert hex numbers to class names', () => {
    expect(getFinalRecordClass('0x1')).toBe('IN');
    expect(getFinalRecordClass('0x3')).toBe('CH');
  });

  it('should convert hex numbers to known class names', () => {
    // 0xFF = 255 = ANY in the CLASS_MAP
    expect(getFinalRecordClass('0xFF')).toBe('ANY');
  });

  it('should convert unknown hex numbers to CLASS format', () => {
    expect(getFinalRecordClass('0xFFF')).toBe('CLASS4095');
  });
});

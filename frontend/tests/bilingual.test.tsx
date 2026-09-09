import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BilingualLabel, BilingualText } from '../src/components/primitives/Bilingual';
import { NAV_SECTIONS } from '../src/layout/nav';

describe('bilingual UI contract', () => {
  it('renders adjacent Chinese and English labels with language attributes', () => {
    render(<BilingualLabel zh="诊断结论" en="Diagnosis" />);
    expect(screen.getByText('诊断结论')).toHaveAttribute('lang', 'zh-CN');
    expect(screen.getByText('Diagnosis')).toHaveAttribute('lang', 'en');
  });

  it('renders explanatory copy on separate bilingual lines', () => {
    render(<BilingualText zh="缺少参考证据" en="Missing reference evidence" />);
    expect(screen.getByText('缺少参考证据')).toHaveAttribute('lang', 'zh-CN');
    expect(screen.getByText('Missing reference evidence')).toHaveAttribute('lang', 'en');
  });

  it('keeps every navigation item paired', () => {
    for (const section of NAV_SECTIONS) {
      expect(section.labelZh).toBeTruthy();
      expect(section.labelEn).toBeTruthy();
      for (const item of section.items) {
        expect(item.labelZh).toBeTruthy();
        expect(item.labelEn).toBeTruthy();
      }
    }
  });

});

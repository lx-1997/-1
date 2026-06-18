import React from 'react';
declare function require(module: string): any;
const rtl = (require('react-i18next') as any);

export function useT(): (key: string) => string {
  const { t } = rtl.useTranslation();
  return (key: string): string => String(t(key) || key);
}

import React from 'react';
import { ACCENT_THEME_OPTIONS, useTheme } from '../context/ThemeContext';
import './AccentThemePicker.css';

interface AccentThemePickerProps {
  compact?: boolean;
  onChange?: () => void;
}

const AccentThemePicker: React.FC<AccentThemePickerProps> = ({ compact = false, onChange }) => {
  const { theme, accentTheme, setAccentTheme } = useTheme();
  const current = ACCENT_THEME_OPTIONS.find(option => option.value === accentTheme) || ACCENT_THEME_OPTIONS[0];

  return (
    <div className={`df-accent-picker${compact ? ' is-compact' : ''}`}>
      <div className="df-accent-picker__head">
        <span>主题颜色</span>
        <b>{current.label}</b>
      </div>
      <div className="df-accent-picker__options" role="radiogroup" aria-label="选择主题颜色">
        {ACCENT_THEME_OPTIONS.map(option => (
          <button
            key={option.value}
            type="button"
            className={accentTheme === option.value ? 'is-active' : ''}
            aria-label={option.label}
            aria-pressed={accentTheme === option.value}
            title={option.label}
            onClick={() => {
              setAccentTheme(option.value);
              onChange?.();
            }}
          >
            <span style={{ background: theme === 'dark' ? option.dark : option.light }} />
          </button>
        ))}
      </div>
    </div>
  );
};

export default AccentThemePicker;

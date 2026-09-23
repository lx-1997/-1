import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AccentThemePicker from '../components/AccentThemePicker';
import { ThemeProvider } from './ThemeContext';

describe('ThemeProvider accent themes', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute('data-accent');
    document.documentElement.removeAttribute('data-theme');
  });

  it('defaults to light mode and persists it under the v3 theme key', async () => {
    render(<ThemeProvider><div /></ThemeProvider>);

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'light'));
    expect(window.localStorage.getItem('deepfocus.theme.v3')).toBe('light');
  });

  it('restores an explicit dark mode choice', async () => {
    window.localStorage.setItem('deepfocus.theme.v3', 'dark');

    render(<ThemeProvider><div /></ThemeProvider>);

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'dark'));
  });

  it('defaults to Apple blue and persists another selected accent', async () => {
    render(
      <ThemeProvider>
        <AccentThemePicker />
      </ThemeProvider>
    );

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-accent', 'blue'));
    expect(screen.getByRole('button', { name: '经典蓝' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '紫色' }));

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-accent', 'purple'));
    expect(window.localStorage.getItem('deepfocus.accent.v1')).toBe('purple');
    expect(screen.getByRole('button', { name: '紫色' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('restores a saved accent choice', async () => {
    window.localStorage.setItem('deepfocus.accent.v1', 'green');

    render(
      <ThemeProvider>
        <AccentThemePicker />
      </ThemeProvider>
    );

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-accent', 'green'));
    expect(screen.getByRole('button', { name: '绿色' })).toHaveAttribute('aria-pressed', 'true');
  });
});

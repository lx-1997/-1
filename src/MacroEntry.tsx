import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from './context/ThemeContext';
import { AuthProvider } from './context/AuthContext';
import ErrorBoundary from './components/ErrorBoundary';
import MacroStandalone from './components/MacroStandalone';
import './index.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode><BrowserRouter><ThemeProvider><AuthProvider><ErrorBoundary>
    <MacroStandalone />
  </ErrorBoundary></AuthProvider></ThemeProvider></BrowserRouter></React.StrictMode>
);

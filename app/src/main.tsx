import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
// base Nuova (card, pillole, bottoni): una sola copia e prima di App, cosi' finisce
// prima dei fogli di pagina (importati da App) e le loro regole vincono sempre
import './components/nuova/nuova.css';
import App from './App';
import './index.css';
import './components/theme-tokens.css';
import { applyThemeAttribute, readInterfaceTheme } from './lib/interface-theme';

// Catch global unhandled errors per debugging
window.addEventListener('error', (e) => {
  console.error('[GLOBAL ERROR]', e.error || e.message, e.filename, e.lineno);
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('[UNHANDLED PROMISE REJECTION]', e.reason);
});

// Paint the saved theme before the first render so the dark Nuova never flashes light.
applyThemeAttribute(readInterfaceTheme().theme);

// NOTE: StrictMode disabilitato perche' in dev mode esegue gli effects 2 volte
// causando duplicate fetch SSE che raddoppiano il testo nelle chat.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <HashRouter>
    <App />
  </HashRouter>
);

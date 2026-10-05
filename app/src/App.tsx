import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import LoginGate from './components/LoginGate';
import LinguaGate from './components/LinguaGate';
import Dashboard from './pages/Dashboard';
import MemoArchive from './pages/MemoArchive';
import Chat from './pages/Chat';
import Decisions from './pages/Decisions';
import AgentsLive from './pages/AgentsLive';
import TradeIdeaPage from './pages/TradeIdeaPage';
import MonteCarloPage from './pages/MonteCarloPage';
import TradeEntryPage from './pages/TradeEntryPage';
import NewsPage from './pages/NewsPage';
import FactorsPage from './pages/FactorsPage';
import PerformancePage from './pages/PerformancePage';
import VolSurfacePage from './pages/VolSurfacePage';
import EdgeScannerPage from './pages/EdgeScannerPage';
import MovementsPage from './pages/MovementsPage';
import MarketPage from './pages/MarketPage';
import WatchlistPage from './pages/WatchlistPage';
import FundamentalsPage from './pages/FundamentalsPage';
import FilingPage from './pages/FilingPage';
import AvvisiNotizie from './components/AvvisiNotizie';
import CommandPalette from './components/CommandPalette';
import MandatoGate from './components/MandatoGate';
import MandatoPage from './pages/MandatoPage';
import AgentProgressPage from './pages/AgentProgressPage';
import { PAGE_DESTINATIONS, localizeDestination } from './lib/navigation';
import { useLingua, useT } from './i18n/provider';
import { InterfaceThemeProvider } from './components/InterfaceThemeProvider';

const PAGES: Record<string, React.ComponentType> = {
  dashboard: Dashboard, performance: PerformancePage, watchlist: WatchlistPage,
  market: MarketPage, news: NewsPage, fundamentals: FundamentalsPage, filing: FilingPage,
  factors: FactorsPage, montecarlo: MonteCarloPage, vol: VolSurfacePage, edge: EdgeScannerPage,
  chat: Chat, agents: AgentsLive, progress: AgentProgressPage, memos: MemoArchive,
  decisions: Decisions, trades: TradeEntryPage, movements: MovementsPage, mandato: MandatoPage,
};

// Polling degli avvisi + avviso in app: vive dentro il router per aprire la notizia.
function AlertsRunner() {
  return <AvvisiNotizie />;
}

function RoutedDestination({ entry, Page }: {
  entry: (typeof PAGE_DESTINATIONS)[number];
  Page: React.ComponentType<any>;
}) {
  const language = useLingua();
  return (
    <ErrorBoundary
      label={localizeDestination(entry, language).label}
      propagate={entry.id === 'dashboard'}
    >
      <Page />
    </ErrorBoundary>
  );
}

export default function App() {
  const t = useT();
  return (
    <ErrorBoundary label={t('shell.app')}>
      <InterfaceThemeProvider>
        <LoginGate>
          <LinguaGate>
            <MandatoGate>
              <AlertsRunner />
              <CommandPalette />
              <Layout>
                <Routes>
                  <Route path="/" element={<Navigate to="/dashboard" replace />} />
                  {PAGE_DESTINATIONS.map(entry => {
                    const Page = PAGES[entry.id];
                    return <Route key={entry.id} path={entry.to} element={<RoutedDestination entry={entry} Page={Page} />} />;
                  })}
                  <Route path="/agents/trade-idea" element={<ErrorBoundary label={t('tradeidea.title')}><TradeIdeaPage /></ErrorBoundary>} />
                  <Route path="/settings" element={<Navigate to="/dashboard" replace />} />
                </Routes>
              </Layout>
            </MandatoGate>
          </LinguaGate>
        </LoginGate>
      </InterfaceThemeProvider>
    </ErrorBoundary>
  );
}

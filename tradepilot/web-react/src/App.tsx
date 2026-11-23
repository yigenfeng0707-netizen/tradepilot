import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Layout from '@/components/Layout';
import ErrorBoundary from '@/components/ErrorBoundary';
import Home from '@/pages/Home';
import MarketInsight from '@/pages/MarketInsight';
import SmartSelection from '@/pages/SmartSelection';
import ContentGeneration from '@/pages/ContentGeneration';
import Compliance from '@/pages/Compliance';
import CustomerService from '@/pages/CustomerService';
import Integrations from '@/pages/Integrations';
import IntegrationCallback from '@/pages/IntegrationCallback';
import Account from '@/pages/Account';
import Pipeline from '@/pages/Pipeline';
import Pricing from '@/pages/Pricing';
import Admin from '@/pages/Admin';
import Login from '@/pages/Login';

export default function App() {
  // 生产形态是 FastAPI 把 dist 挂在 /app 下（同源托管，无 CORS、无端口代理）。
  // 此时 URL 是 /app/smart-selection，而 Router 若没有 basename 就匹配不到任何路由
  // ——表现是整页白屏（实测踩过）。运行时按实际路径推断，dev 与 prod 同一份代码。
  const basename = window.location.pathname.startsWith('/app') ? '/app' : '/';

  return (
    <ErrorBoundary>
      <Router basename={basename}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<ErrorBoundary><Home /></ErrorBoundary>} />
            <Route path="/market-insight" element={<ErrorBoundary><MarketInsight /></ErrorBoundary>} />
            <Route path="/smart-selection" element={<ErrorBoundary><SmartSelection /></ErrorBoundary>} />
            <Route path="/content-generation" element={<ErrorBoundary><ContentGeneration /></ErrorBoundary>} />
            <Route path="/compliance" element={<ErrorBoundary><Compliance /></ErrorBoundary>} />
            <Route path="/customer-service" element={<ErrorBoundary><CustomerService /></ErrorBoundary>} />
            <Route path="/integrations" element={<ErrorBoundary><Integrations /></ErrorBoundary>} />
            <Route path="/integrations/callback" element={<ErrorBoundary><IntegrationCallback /></ErrorBoundary>} />
            <Route path="/account" element={<ErrorBoundary><Account /></ErrorBoundary>} />
            <Route path="/pipeline" element={<ErrorBoundary><Pipeline /></ErrorBoundary>} />
            <Route path="/pricing" element={<ErrorBoundary><Pricing /></ErrorBoundary>} />
            <Route path="/admin" element={<ErrorBoundary><Admin /></ErrorBoundary>} />
            <Route path="/login" element={<ErrorBoundary><Login /></ErrorBoundary>} />
          </Route>
        </Routes>
      </Router>
    </ErrorBoundary>
  );
}

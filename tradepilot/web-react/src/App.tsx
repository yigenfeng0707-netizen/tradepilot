import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Layout from '@/components/Layout';
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
    <Router basename={basename}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Home />} />
          <Route path="/market-insight" element={<MarketInsight />} />
          <Route path="/smart-selection" element={<SmartSelection />} />
          <Route path="/content-generation" element={<ContentGeneration />} />
          <Route path="/compliance" element={<Compliance />} />
          <Route path="/customer-service" element={<CustomerService />} />
          <Route path="/integrations" element={<Integrations />} />
          <Route path="/integrations/callback" element={<IntegrationCallback />} />
          <Route path="/account" element={<Account />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/pricing" element={<Pricing />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/login" element={<Login />} />
        </Route>
      </Routes>
    </Router>
  );
}

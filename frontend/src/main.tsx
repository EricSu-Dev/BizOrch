import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import { App } from './app/App'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#167d73',
          colorInfo: '#167d73',
          colorText: '#142b2b',
          colorBgLayout: '#f2f4ef',
          borderRadius: 10,
          fontFamily:
            "Inter, 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)

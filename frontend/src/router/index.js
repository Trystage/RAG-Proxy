import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', component: () => import('../views/Login.vue') },
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { titleKey: 'nav.dashboard' } },
      { path: 'providers', component: () => import('../views/Providers.vue'), meta: { titleKey: 'nav.providers' } },
      { path: 'kbs', component: () => import('../views/KnowledgeBases.vue'), meta: { titleKey: 'nav.kbs' } },
      { path: 'mappings', component: () => import('../views/Mappings.vue'), meta: { titleKey: 'nav.mappings' } },
      { path: 'logs', component: () => import('../views/Logs.vue'), meta: { titleKey: 'nav.logs' } },
      { path: 'api-keys', component: () => import('../views/ApiKeys.vue'), meta: { titleKey: 'nav.apiKeys' } },
      { path: 'settings', component: () => import('../views/Settings.vue'), meta: { titleKey: 'nav.settings' } }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const token = localStorage.getItem('ragproxy_token')
  if (to.path !== '/login' && !token) {
    return '/login'
  }
  if (to.path === '/login' && token) {
    return '/dashboard'
  }
  return true
})

export default router

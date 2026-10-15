import { createI18n } from 'vue-i18n'
import zh from './zh'
import en from './en'

const saved = localStorage.getItem('ragproxy_locale')
const locale = saved === 'en' || saved === 'zh' ? saved : 'zh'

export const i18n = createI18n({
  legacy: false,
  locale,
  fallbackLocale: 'zh',
  messages: { zh, en }
})

export function setLocale(lang) {
  const next = lang === 'en' ? 'en' : 'zh'
  i18n.global.locale.value = next
  localStorage.setItem('ragproxy_locale', next)
  document.documentElement.lang = next === 'zh' ? 'zh-CN' : 'en'
  document.title = i18n.global.t('appTitle')
}

document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
document.title = i18n.global.t('appTitle')

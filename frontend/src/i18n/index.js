import { createI18n } from 'vue-i18n'
import zh from './zh'
import en from './en'
import ru from './ru'
import es from './es'
import pt from './pt'

const supportedLocales = ['zh', 'en', 'ru', 'es', 'pt']
const saved = localStorage.getItem('ragproxy_locale')
const locale = supportedLocales.includes(saved) ? saved : 'zh'

export const i18n = createI18n({
  legacy: false,
  locale,
  fallbackLocale: 'zh',
  messages: { zh, en, ru, es, pt }
})

export function setLocale(lang) {
  const next = supportedLocales.includes(lang) ? lang : 'zh'
  i18n.global.locale.value = next
  localStorage.setItem('ragproxy_locale', next)
  document.documentElement.lang = next === 'zh' ? 'zh-CN' : next
  document.title = i18n.global.t('appTitle')
}

document.documentElement.lang = locale === 'zh' ? 'zh-CN' : locale
document.title = i18n.global.t('appTitle')

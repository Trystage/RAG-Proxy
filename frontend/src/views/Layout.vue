<template>
  <el-container style="height: 100vh">
    <el-aside width="210px" class="aside">
      <div class="logo">
        <span class="logo-text">RAG Proxy</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="transparent"
        text-color="#a3aec9"
        active-text-color="#F7A8C4"
      >
        <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon><span>{{ t('nav.dashboard') }}</span></el-menu-item>
        <el-menu-item index="/providers"><el-icon><Connection /></el-icon><span>{{ t('nav.providers') }}</span></el-menu-item>
        <el-menu-item index="/kbs"><el-icon><FolderOpened /></el-icon><span>{{ t('nav.kbs') }}</span></el-menu-item>
        <el-menu-item index="/mappings"><el-icon><Share /></el-icon><span>{{ t('nav.mappings') }}</span></el-menu-item>
        <el-menu-item index="/logs"><el-icon><Document /></el-icon><span>{{ t('nav.logs') }}</span></el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon><span>{{ t('nav.settings') }}</span></el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="header-title">{{ $route.meta.titleKey ? t($route.meta.titleKey) : '' }}</span>
        <div class="header-actions">
          <el-button text @click="toggleLocale">{{ locale === 'zh' ? 'EN' : '中文' }}</el-button>
          <el-button text @click="logout">{{ t('nav.logout') }}</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { setLocale } from '../i18n'

const router = useRouter()
const { t, locale } = useI18n()
function logout() {
  localStorage.removeItem('ragproxy_token')
  router.push('/login')
}
function toggleLocale() {
  setLocale(locale.value === 'zh' ? 'en' : 'zh')
}
</script>

<style scoped>
.aside {
  background: linear-gradient(180deg, rgba(46, 58, 88, 0.92) 0%, rgba(31, 45, 61, 0.94) 58%, rgba(46, 58, 88, 0.92) 100%);
  position: relative;
  overflow: hidden;
}
.aside::after {
  content: '';
  position: absolute;
  top: -60px;
  right: -80px;
  width: 160px;
  height: 160px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(247, 168, 196, 0.12), transparent 70%);
  pointer-events: none;
}
.aside::before {
  content: '';
  position: absolute;
  bottom: -70px;
  left: -50px;
  width: 150px;
  height: 150px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(143, 199, 232, 0.10), transparent 70%);
  pointer-events: none;
}
.logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  position: relative;
  z-index: 1;
}
.logo-text {
  font-size: 19px;
  font-weight: 800;
  letter-spacing: 1px;
  background: linear-gradient(100deg, #F7A8C4 10%, #9FD0F2 90%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.aside :deep(.el-menu) {
  border-right: none;
  position: relative;
  z-index: 1;
}
.aside :deep(.el-menu-item) {
  margin: 4px 10px;
  border-radius: 10px;
}
.aside :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.08);
}
.aside :deep(.el-menu-item.is-active) {
  background: rgba(247, 168, 196, 0.18);
  color: #F7A8C4;
}
.header {
  background: rgba(233, 246, 238, 0.88);
  border-bottom: 1px solid rgba(200, 228, 208, 0.6);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title {
  font-size: 16px;
  font-weight: 700;
  background: linear-gradient(100deg, #e96f9f, #5b9fd8);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.main {
  background: transparent;
}
</style>

<!-- 全局磨砂玻璃：作用于除登录页外的所有后台页面 -->
<style>
body {
  background:
    radial-gradient(820px 520px at 12% 6%, rgba(247, 168, 196, 0.32), transparent 62%),
    radial-gradient(920px 560px at 88% 10%, rgba(143, 199, 232, 0.36), transparent 64%),
    radial-gradient(720px 520px at 45% 96%, rgba(228, 220, 245, 0.40), transparent 60%),
    #eef3fa;
  background-attachment: fixed;
}

/* 卡片：磨砂玻璃（登录页除外） */
.el-card:not(.login-card) {
  background: rgba(236, 248, 240, 0.92) !important;
  border: 1px solid rgba(200, 228, 208, 0.7) !important;
  box-shadow: 0 10px 30px rgba(90, 130, 110, 0.12) !important;
}
.el-card:not(.login-card) .el-card__header {
  background: rgba(228, 243, 234, 0.45) !important;
  border-bottom: 1px solid rgba(255, 255, 255, 0.45) !important;
}

/* 按钮：玻璃质感 */
.el-button--primary {
  background: linear-gradient(120deg, #F57EA8, #7FB8E8) !important;
  border-color: rgba(255, 255, 255, 0.55) !important;
  box-shadow: 0 6px 18px rgba(247, 168, 196, 0.35) !important;
  color: #fff !important;
}
.el-button--primary:hover {
  filter: brightness(1.07);
  box-shadow: 0 8px 22px rgba(143, 199, 232, 0.45) !important;
}
.el-button:not(.el-button--primary):not(.el-button--text) {
  background: rgba(244, 252, 247, 0.95) !important;
  border: 1px solid rgba(200, 228, 208, 0.8) !important;
  color: #4a5a50 !important;
  box-shadow: 0 4px 14px rgba(90, 130, 110, 0.08) !important;
}
.el-button:not(.el-button--primary):not(.el-button--text):hover {
  background: rgba(255, 255, 255, 0.92) !important;
  color: #F57EA8 !important;
}

/* 表格：行透明融入玻璃卡片 */
.el-table {
  background: transparent !important;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-row-hover-bg-color: rgba(247, 168, 196, 0.10);
  --el-table-header-bg-color: rgba(255, 255, 255, 0.40);
}
.el-table th.el-table__cell {
  background: rgba(226, 241, 232, 0.55) !important;
  color: #6E6E80 !important;
}
.el-table td.el-table__cell {
  background: transparent !important;
}
.el-table--enable-row-hover .el-table__body tr:hover > td.el-table__cell {
  background: rgba(247, 168, 196, 0.10) !important;
}

/* 输入框：半透明白玻璃 */
.el-input__wrapper {
  background: rgba(243, 251, 246, 0.80) !important;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.65) inset !important;
}
.el-textarea__inner {
  background: rgba(243, 251, 246, 0.80) !important;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.65) inset !important;
}

/* 对话框与抽屉：护眼淡绿底（不用 backdrop-filter，避免内容渲染异常） */
.el-dialog {
  background: rgba(240, 249, 243, 0.98) !important;
  border: 1px solid rgba(200, 228, 208, 0.7);
  border-radius: 14px;
  box-shadow: 0 20px 60px rgba(90, 130, 110, 0.20) !important;
  max-height: 88vh;
  overflow: auto;
}
.el-dialog__header {
  background: rgba(224, 241, 230, 0.55);
  border-bottom: 1px solid rgba(200, 228, 208, 0.55);
  margin-right: 0 !important;
}
.el-dialog__body {
  background: transparent;
}
.el-drawer {
  background: rgba(240, 249, 243, 0.98) !important;
  border: 1px solid rgba(200, 228, 208, 0.7);
}
</style>

<template>
  <div class="login-wrap" :style="{ backgroundImage: `url(${yamaBg})` }">
    <div class="login-veil"></div>
    <el-card class="login-card">
      <template #header>
        <div class="login-title">
          <span>{{ t('login.title') }}</span>
          <el-button text size="small" @click="toggleLocale">{{ locale === 'zh' ? 'EN' : '中文' }}</el-button>
        </div>
      </template>
      <el-form @submit.prevent="doLogin">
        <el-form-item :label="t('login.key')">
          <el-input
            v-model="key"
            type="password"
            show-password
            :placeholder="t('login.placeholder')"
            @keyup.enter="doLogin"
          />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="loading" @click="doLogin">
          {{ t('login.submit') }}
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import http, { errMsg } from '../api'
import { setLocale } from '../i18n'
import yamaBg from '../assets/yama-beautified.jpg'

const router = useRouter()
const { t, locale } = useI18n()
function toggleLocale() {
  setLocale(locale.value === 'zh' ? 'en' : 'zh')
}
const key = ref('')
const loading = ref(false)

async function doLogin() {
  if (!key.value) return
  loading.value = true
  try {
    const { data } = await http.post('/admin/auth/login', { key: key.value })
    localStorage.setItem('ragproxy_token', data.token)
    ElMessage.success(t('login.success'))
    router.push('/dashboard')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background-size: cover;
  background-position: center 22%;
  position: relative;
}
.login-veil {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    rgba(24, 34, 46, 0.48) 0%,
    rgba(52, 73, 94, 0.32) 46%,
    rgba(247, 168, 196, 0.22) 78%,
    rgba(143, 199, 232, 0.22) 100%
  );
}
.login-card {
  width: 400px;
  position: relative;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.52);
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.55);
  box-shadow: 0 18px 50px rgba(10, 18, 28, 0.32), inset 0 1px 0 rgba(255, 255, 255, 0.7);
  overflow: hidden;
}
.login-card :deep(.el-card__header) {
  background: rgba(255, 255, 255, 0.30);
  border-bottom: 1px solid rgba(255, 255, 255, 0.35);
}
.login-card :deep(.el-card__body) {
  background: transparent;
}
.login-card :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.6) inset;
}
.login-title {
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
</style>

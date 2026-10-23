<template>
	<view class="page">
		<view class="tip-banner" v-if="messages.length === 0">
			<view class="tip-title">🏥 欢迎使用健康分诊助手</view>
			<view class="tip-desc">请描述您的症状，我会为您分诊并给出建议。</view>
			<view class="quick-list">
				<view class="quick-item" v-for="(q, i) in quickQuestions" :key="i" @click="sendQuick(q)">
					{{ q }}
				</view>
			</view>
		</view>

		<view class="new-chat-btn" v-if="messages.length > 0" @click="newChat">
			<text class="new-chat-icon">＋</text>
			<text>新建对话</text>
		</view>

		<scroll-view
			class="msg-scroll"
			scroll-y
			:scroll-into-view="scrollIntoId"
			:scroll-with-animation="true"
			:enable-flex="true"
		>
			<view class="msg-list">
				<view
					class="msg-item"
					:class="msg.role === 'user' ? 'msg-right' : 'msg-left'"
					:id="'msg-' + msg.id"
					v-for="msg in messages"
					:key="msg.id"
				>
					<view class="avatar" :class="msg.role === 'user' ? 'avatar-user' : 'avatar-ai'">
						{{ msg.role === 'user' ? '我' : 'AI' }}
					</view>
					<view class="bubble" :class="msg.role === 'user' ? 'bubble-user' : 'bubble-ai'">
						<text class="bubble-text">{{ msg.content }}</text>
						<view class="stream-dot" v-if="msg.streaming">
							<view class="dot" v-for="n in 3" :key="n"></view>
						</view>
					</view>
				</view>

				<!-- 急诊中断卡片 -->
				<view class="interrupt-card" v-if="showEmergency">
					<view class="interrupt-icon">🚨</view>
					<view class="interrupt-text">{{ emergencyAdvice }}</view>
					<view class="interrupt-btns">
						<view class="btn btn-danger" @click="handleEmergency('confirm')">需要紧急帮助</view>
						<view class="btn btn-secondary" @click="handleEmergency('cancel')">暂不需要</view>
					</view>
				</view>

				<!-- 地址选择中断卡片 -->
				<view class="interrupt-card interrupt-card-addr" v-if="showAddressSelect">
					<view class="interrupt-icon">📍</view>
					<view class="interrupt-text">{{ addressPrompt }}</view>
					<view class="addr-picker">
						<picker mode="selector" :range="provinceNames" @change="onProvinceChange">
							<view class="picker-box">
								{{ selectedProvince || '请选择省份' }}
								<text class="picker-arrow">▾</text>
							</view>
						</picker>
						<picker mode="selector" :range="cityOptions" @change="onCityChange">
							<view class="picker-box">
								{{ selectedCity || '请选择城市' }}
								<text class="picker-arrow">▾</text>
							</view>
						</picker>
					</view>
					<view class="interrupt-btns">
						<view class="btn btn-danger" @click="confirmAddress" :class="{ disabled: !selectedCity }">确认并推荐医院</view>
				</view>
			</view>

			<!-- 症状追问中断卡片 -->
			<view class="interrupt-card interrupt-card-followup" v-if="showFollowup">
				<view class="interrupt-icon">❓</view>
				<view class="interrupt-text">{{ followupPrompt }}</view>
				<view class="addr-picker">
					<textarea class="followup-input" v-model="followupInput" placeholder="请补充症状..." :auto-height="true" />
				</view>
				<view class="interrupt-btns">
					<view class="btn btn-danger" @click="confirmFollowup" :class="{ disabled: !followupInput.trim() }">提交补充</view>
				</view>
			</view>
		</view>
	</scroll-view>

		<view class="input-bar" :style="{ paddingBottom: safeBottom + 'px' }">
			<textarea
				class="input"
				v-model="inputText"
				placeholder="请描述您的症状..."
				:auto-height="true"
				:maxlength="500"
				:disabled="isLoading"
				confirm-type="send"
				@confirm="handleSend"
				@focus="onInputFocus"
			/>
			<view
				class="send-btn"
				:class="{ disabled: !inputText.trim() || isLoading }"
				@click="handleSend"
			>
				发送
			</view>
		</view>
	</view>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { PROVINCES } from './regions.js'

const BASE_URL = '/api'

const messages = ref([])
const inputText = ref('')
const isLoading = ref(false)
const threadId = ref('')
const interruptThreadId = ref('')
const scrollIntoId = ref('')
const safeBottom = ref(0)

// 急诊中断
const showEmergency = ref(false)
const emergencyAdvice = ref('')

// 地址选择中断
const showAddressSelect = ref(false)
const addressPrompt = ref('')
const selectedProvince = ref('')
const selectedCity = ref('')

// 症状追问中断
const showFollowup = ref(false)
const followupPrompt = ref('')
const followupInput = ref('')

const quickQuestions = [
	'我头疼发烧三天了',
	'摔伤了腿伤口很深',
	'孩子一直咳嗽',
	'感冒多喝水有用吗'
]

// 省市联动
const provinceNames = computed(() => PROVINCES.map(p => p.name))
const cityOptions = computed(() => {
	const p = PROVINCES.find(x => x.name === selectedProvince.value)
	return p ? p.cities : []
})

const onProvinceChange = (e) => {
	selectedProvince.value = provinceNames.value[e.detail.value]
	selectedCity.value = ''
}
const onCityChange = (e) => {
	selectedCity.value = cityOptions.value[e.detail.value]
}

let msgIdSeq = 0
const genId = () => ++msgIdSeq

const addMessage = (role, content, streaming = false) => {
	const id = genId()
	messages.value.push({ id, role, content, streaming })
	nextTick(() => {
		scrollIntoId.value = 'msg-' + id
	})
	return id
}

const updateMessage = (id, patch) => {
	const idx = messages.value.findIndex(m => m.id === id)
	if (idx !== -1) {
		messages.value[idx] = { ...messages.value[idx], ...patch }
	}
}

const appendToMessage = (id, chunk) => {
	const idx = messages.value.findIndex(m => m.id === id)
	if (idx !== -1) {
		messages.value[idx].content += chunk
	}
}

const scrollToBottom = () => {
	if (messages.value.length === 0) return
	const last = messages.value[messages.value.length - 1]
	nextTick(() => {
		scrollIntoId.value = 'msg-' + last.id
	})
}

const parseChunk = (line) => {
	try {
		return JSON.parse(line)
	} catch (e) {
		return null
	}
}

const genThreadId = () => {
	return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
		const r = Math.random() * 16 | 0
		const v = c === 'x' ? r : (r & 0x3 | 0x8)
		return v.toString(16)
	})
}

// 处理一行 JSON 数据
const handleStreamData = (data, ctx) => {
	if (!data) return
	if (data.err) {
		uni.showToast({ title: data.err, icon: 'none' })
		return
	}
	if (data.interrupt) {
		ctx.gotInterrupt = true
		interruptThreadId.value = data.thread_id
		const type = data.type || ''
		const payload = data.payload || {}
		if (type === 'address_select') {
			showAddressSelect.value = true
			addressPrompt.value = payload.prompt || '请选择您所在的城市'
		} else if (type === 'followup') {
			showFollowup.value = true
			followupPrompt.value = payload.prompt || '请补充更多症状'
		} else {
			// 默认急诊中断
			showEmergency.value = true
			emergencyAdvice.value = payload.advice || payload.message || ''
		}
		return
	}
	if (typeof data.c === 'string') {
		if (ctx.aiMsgId === null) {
			ctx.aiMsgId = addMessage('ai', '', true)
		}
		appendToMessage(ctx.aiMsgId, data.c)
	}
}

const extractLines = (buffer) => {
	const parts = buffer.split('\n')
	const rest = parts.pop()
	return { lines: parts, rest }
}

const streamRequest = async (question, tid) => {
	const url = `${BASE_URL}/chat/stream?question=${encodeURIComponent(question)}&thread_id=${tid}`

	// #ifdef H5
	const response = await fetch(url)
	if (!response.ok) {
		uni.showToast({ title: `请求失败: ${response.status}`, icon: 'none' })
		return { gotInterrupt: false }
	}
	const reader = response.body.getReader()
	const decoder = new TextDecoder()
	let buffer = ''
	const ctx = { aiMsgId: null, gotInterrupt: false }

	while (true) {
		const { done, value } = await reader.read()
		if (done) break
		buffer += decoder.decode(value, { stream: true })
		const { lines, rest } = extractLines(buffer)
		buffer = rest
		for (const line of lines) {
			if (!line.trim()) continue
			handleStreamData(parseChunk(line), ctx)
		}
	}
	if (buffer.trim()) {
		handleStreamData(parseChunk(buffer), ctx)
	}
	if (ctx.aiMsgId !== null) {
		updateMessage(ctx.aiMsgId, { streaming: false })
	}
	return { gotInterrupt: ctx.gotInterrupt }
	// #endif

	// #ifndef H5
	return new Promise((resolve) => {
		uni.request({
			url,
			method: 'GET',
			success: (res) => {
				const lines = (res.data || '').split('\n').filter(l => l.trim())
				const ctx = { aiMsgId: null, gotInterrupt: false }
				for (const line of lines) {
					handleStreamData(parseChunk(line), ctx)
				}
				if (ctx.aiMsgId !== null) {
					updateMessage(ctx.aiMsgId, { streaming: false })
				}
				resolve({ gotInterrupt: ctx.gotInterrupt })
			},
			fail: () => {
				uni.showToast({ title: '网络错误', icon: 'none' })
				resolve({ gotInterrupt: false })
			}
		})
	})
	// #endif
}

const resumeRequest = async (tid, value) => {
	const url = `${BASE_URL}/chat/resume`

	// #ifdef H5
	const response = await fetch(url, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ thread_id: tid, resume_value: value })
	})
	if (!response.ok) {
		uni.showToast({ title: `请求失败: ${response.status}`, icon: 'none' })
		return
	}
	const reader = response.body.getReader()
	const decoder = new TextDecoder()
	let buffer = ''
	const ctx = { aiMsgId: null, gotInterrupt: false }

	while (true) {
		const { done, value } = await reader.read()
		if (done) break
		buffer += decoder.decode(value, { stream: true })
		const { lines, rest } = extractLines(buffer)
		buffer = rest
		for (const line of lines) {
			if (!line.trim()) continue
			handleStreamData(parseChunk(line), ctx)
		}
	}
	if (buffer.trim()) {
		handleStreamData(parseChunk(buffer), ctx)
	}
	if (ctx.aiMsgId !== null) {
		updateMessage(ctx.aiMsgId, { streaming: false })
	}
	return
	// #endif

	// #ifndef H5
	return new Promise((resolve) => {
		uni.request({
			url,
			method: 'POST',
			header: { 'Content-Type': 'application/json' },
			data: { thread_id: tid, resume_value: value },
			success: (res) => {
				const lines = (res.data || '').split('\n').filter(l => l.trim())
				const ctx = { aiMsgId: null, gotInterrupt: false }
				for (const line of lines) {
					handleStreamData(parseChunk(line), ctx)
				}
				if (ctx.aiMsgId !== null) {
					updateMessage(ctx.aiMsgId, { streaming: false })
				}
				resolve()
			},
			fail: () => {
				uni.showToast({ title: '网络错误', icon: 'none' })
				resolve()
			}
		})
	})
	// #endif
}

const handleSend = async () => {
	const text = inputText.value.trim()
	if (!text || isLoading.value) return
	if (showEmergency.value || showAddressSelect.value || showFollowup.value) {
		uni.showToast({ title: '请先处理当前提示', icon: 'none' })
		return
	}

	if (!threadId.value) {
		threadId.value = genThreadId()
	}

	inputText.value = ''
	addMessage('user', text)
	isLoading.value = true

	await streamRequest(text, threadId.value)

	isLoading.value = false
	scrollToBottom()
}

const sendQuick = (q) => {
	inputText.value = q
	handleSend()
}

// 急诊卡片按钮
const handleEmergency = async (value) => {
	if (!interruptThreadId.value) return
	showEmergency.value = false
	isLoading.value = true
	await resumeRequest(interruptThreadId.value, value)
	// 不清空 interruptThreadId — 如果 resume 后又触发了 address_select 中断，
	// handleStreamData 会重新设置它；如果没有后续中断，confirmAddress 会清空
	isLoading.value = false
	scrollToBottom()
}

// 地址确认
const confirmAddress = async () => {
	if (!selectedCity.value) return
	if (!interruptThreadId.value) return

	showAddressSelect.value = false
	isLoading.value = true
	await resumeRequest(interruptThreadId.value, selectedCity.value)
	interruptThreadId.value = ''
	isLoading.value = false
	selectedProvince.value = ''
	selectedCity.value = ''
	scrollToBottom()
}

// 症状追问提交
const confirmFollowup = async () => {
	const text = followupInput.value.trim()
	if (!text) return
	if (!interruptThreadId.value) return

	showFollowup.value = false
	isLoading.value = true
	await resumeRequest(interruptThreadId.value, text)
	// resume 后可能再次追问(第2次)或结束；若无新中断则清空
	if (!showFollowup.value && !showEmergency.value && !showAddressSelect.value) {
		interruptThreadId.value = ''
	}
	followupInput.value = ''
	isLoading.value = false
	scrollToBottom()
}

// 新建对话
const newChat = () => {
	if (isLoading.value) return
	messages.value = []
	threadId.value = ''
	interruptThreadId.value = ''
	showEmergency.value = false
	showAddressSelect.value = false
	showFollowup.value = false
	followupPrompt.value = ''
	followupInput.value = ''
	selectedProvince.value = ''
	selectedCity.value = ''
}

const onInputFocus = () => {
	setTimeout(scrollToBottom, 300)
}

onMounted(() => {
	const sys = uni.getSystemInfoSync()
	safeBottom.value = sys.safeAreaInsets ? sys.safeAreaInsets.bottom : 0
})
</script>

<style lang="scss" scoped>
.page {
	display: flex;
	flex-direction: column;
	height: 100vh;
	background: #f5f7fa;
}

.tip-banner {
	margin: 32rpx 32rpx 0;
	padding: 36rpx 32rpx;
	background: linear-gradient(135deg, #4A90E2, #357ABD);
	border-radius: 20rpx;
	color: #fff;
	box-shadow: 0 8rpx 24rpx rgba(74, 144, 226, 0.2);
}

.new-chat-btn {
	margin: 24rpx 24rpx 0;
	padding: 16rpx 28rpx;
	background: #fff;
	border-radius: 32rpx;
	display: inline-flex;
	align-items: center;
	gap: 8rpx;
	font-size: 26rpx;
	color: #4A90E2;
	font-weight: 500;
	box-shadow: 0 2rpx 12rpx rgba(0, 0, 0, 0.06);
	align-self: flex-start;
}
.new-chat-btn:active {
	background: #f0f0f0;
}
.new-chat-icon {
	font-size: 32rpx;
	font-weight: 400;
}
.tip-title {
	font-size: 34rpx;
	font-weight: 600;
	margin-bottom: 12rpx;
}
.tip-desc {
	font-size: 26rpx;
	opacity: 0.9;
	margin-bottom: 24rpx;
}
.quick-list {
	display: flex;
	flex-wrap: wrap;
	gap: 16rpx;
}
.quick-item {
	padding: 12rpx 24rpx;
	background: rgba(255, 255, 255, 0.2);
	border-radius: 32rpx;
	font-size: 24rpx;
	backdrop-filter: blur(10px);
}
.quick-item:active {
	background: rgba(255, 255, 255, 0.3);
}

.msg-scroll {
	flex: 1;
	overflow: hidden;
}
.msg-list {
	padding: 24rpx 24rpx 32rpx;
	display: flex;
	flex-direction: column;
	gap: 24rpx;
}

.msg-item {
	display: flex;
	align-items: flex-start;
	max-width: 80%;
}
.msg-left {
	align-self: flex-start;
}
.msg-right {
	align-self: flex-end;
	flex-direction: row-reverse;
}

.avatar {
	width: 72rpx;
	height: 72rpx;
	border-radius: 50%;
	display: flex;
	align-items: center;
	justify-content: center;
	font-size: 26rpx;
	font-weight: 600;
	color: #fff;
	flex-shrink: 0;
}
.avatar-user {
	background: #4A90E2;
	margin-left: 16rpx;
}
.avatar-ai {
	background: #34C759;
	margin-right: 16rpx;
}

.bubble {
	padding: 20rpx 24rpx;
	border-radius: 20rpx;
	font-size: 28rpx;
	line-height: 1.6;
	word-break: break-word;
	white-space: pre-wrap;
	position: relative;
}
.bubble-user {
	background: #4A90E2;
	color: #fff;
	border-top-right-radius: 4rpx;
}
.bubble-ai {
	background: #fff;
	color: #333;
	border-top-left-radius: 4rpx;
	box-shadow: 0 2rpx 8rpx rgba(0, 0, 0, 0.05);
}
.bubble-text {
	font-size: 28rpx;
	line-height: 1.6;
}

.stream-dot {
	display: inline-flex;
	gap: 8rpx;
	margin-left: 8rpx;
	vertical-align: middle;
}
.dot {
	width: 12rpx;
	height: 12rpx;
	border-radius: 50%;
	background: #999;
	animation: bounce 1.2s infinite;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
	0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
	40% { transform: scale(1); opacity: 1; }
}

.interrupt-card {
	margin: 8rpx 0 16rpx;
	padding: 28rpx;
	background: linear-gradient(135deg, #FF6B6B, #EE5A5A);
	border-radius: 20rpx;
	color: #fff;
	align-self: stretch;
	box-shadow: 0 8rpx 24rpx rgba(255, 107, 107, 0.3);
}
.interrupt-card-addr {
	background: linear-gradient(135deg, #4A90E2, #357ABD);
	box-shadow: 0 8rpx 24rpx rgba(74, 144, 226, 0.3);
}
.interrupt-card-followup {
	background: linear-gradient(135deg, #6C5CE7, #5A4BD1);
	box-shadow: 0 8rpx 24rpx rgba(108, 92, 231, 0.3);
}
.followup-input {
	width: 100%;
	min-height: 80rpx;
	padding: 16rpx 24rpx;
	background: rgba(255, 255, 255, 0.95);
	border-radius: 12rpx;
	font-size: 28rpx;
	color: #333;
	box-sizing: border-box;
}
.interrupt-icon {
	font-size: 48rpx;
	margin-bottom: 12rpx;
}
.interrupt-text {
	font-size: 30rpx;
	font-weight: 500;
	line-height: 1.6;
	margin-bottom: 24rpx;
}

.addr-picker {
	display: flex;
	gap: 20rpx;
	margin-bottom: 24rpx;
}
.picker-box {
	flex: 1;
	padding: 16rpx 24rpx;
	background: rgba(255, 255, 255, 0.25);
	border-radius: 12rpx;
	font-size: 28rpx;
	color: #fff;
	display: flex;
	align-items: center;
	justify-content: space-between;
}
.picker-arrow {
	font-size: 24rpx;
	opacity: 0.8;
}

.interrupt-btns {
	display: flex;
	gap: 20rpx;
}
.btn {
	flex: 1;
	padding: 20rpx 0;
	border-radius: 12rpx;
	text-align: center;
	font-size: 28rpx;
	font-weight: 500;
}
.btn-danger {
	background: #fff;
	color: #EE5A5A;
}
.btn-danger.disabled {
	background: rgba(255, 255, 255, 0.4);
	color: rgba(238, 90, 90, 0.5);
}
.btn-danger:active:not(.disabled) {
	background: #f5f5f5;
}
.btn-secondary {
	background: rgba(255, 255, 255, 0.2);
	color: #fff;
}
.btn-secondary:active {
	background: rgba(255, 255, 255, 0.3);
}

.input-bar {
	display: flex;
	align-items: flex-end;
	gap: 16rpx;
	padding: 16rpx 24rpx;
	background: #fff;
	border-top: 1rpx solid #eee;
}
.input {
	flex: 1;
	min-height: 72rpx;
	max-height: 240rpx;
	padding: 18rpx 24rpx;
	background: #f5f7fa;
	border-radius: 16rpx;
	font-size: 28rpx;
	line-height: 1.5;
}
.send-btn {
	padding: 18rpx 36rpx;
	background: #4A90E2;
	color: #fff;
	border-radius: 16rpx;
	font-size: 28rpx;
	font-weight: 500;
	flex-shrink: 0;
}
.send-btn.disabled {
	background: #ccc;
}
.send-btn:active:not(.disabled) {
	background: #357ABD;
}
</style>

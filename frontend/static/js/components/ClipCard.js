const ClipCard = {
  props: ["clip", "time"],
  computed: {
    editing() { return store.clipEdit.id === this.clip.id; },
    clipEdit() { return store.clipEdit; },
    currentSpeakerId() { return store.currentSpeakerId; },
  },
  methods: {
    play() { this.$emit("play", this.clip); },
    startTrim() { store.startTrimEdit(this.clip); store.play(this.clip); },
    addReference() { store.addReference(this.clip); },
    toggle() { store.toggleClip(this.clip); },
    shiftStart(step) { store.shiftTrimStart(step); store.playPreview(); },
    shiftEnd(step) { store.shiftTrimEnd(step); store.playPreview(); },
    saveTrim() { store.saveTrim(); },
    resetTrim() { store.resetTrim(); },
    cancelTrim() { store.cancelTrimEdit(); },
  },
  template: `
    <div class="clip" :class="{kept:clip.selected_speaker_id===currentSpeakerId}" @click="play">
      <div class="score">{{clip.score == null ? '—' : Math.round(clip.score*100)+'%'}}</div>
      <div class="cliptext">{{clip.text}}<small>#{{clip.id}} · {{clip.episode}} · {{time(clip.effective_start != null ? clip.effective_start : clip.start)}} – {{time(clip.effective_end != null ? clip.effective_end : clip.end)}}<span v-if="clip.trim_start||clip.trim_end" style="color:#f59e0b" title="已微调边界"> *</span></small></div>
      <div class="clip-actions" v-if="!editing" @click.stop><button @click="startTrim" title="微调边界">微调</button><button @click="addReference" title="加入参考素材">参考</button><button class="keep" :disabled="!currentSpeakerId" @click="toggle">{{clip.selected_speaker_id===currentSpeakerId?'已保留':'保留'}}</button></div>
      <div v-if="editing" class="trim-toolbar" @click.stop><span class="trim-group"><span class="trim-label">起始</span><button class="trim-btn" @click="shiftStart(-0.25)" title="向左扩展 0.25s">◀◀</button><span class="trim-val">{{clipEdit.trim_start.toFixed(2)}}s</span><button class="trim-btn" @click="shiftStart(0.25)" title="向右收缩 0.25s">▶▶</button></span><span class="trim-group"><span class="trim-label">结束</span><button class="trim-btn" @click="shiftEnd(-0.25)" title="向左收缩 0.25s">◀◀</button><span class="trim-val">{{clipEdit.trim_end.toFixed(2)}}s</span><button class="trim-btn" @click="shiftEnd(0.25)" title="向右扩展 0.25s">▶▶</button></span><button class="trim-save" @click="saveTrim" title="保存边界">💾</button><button class="trim-reset" @click="resetTrim">重置</button><button class="trim-close" @click="cancelTrim">✕</button></div>
    </div>`,
};

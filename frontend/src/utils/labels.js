/**
 * axis_label 표시 헬퍼.
 *   axisLabel(key, source)
 *   - source: API 응답 객체 (config bundle, item, repImagesPayload 등 어떤 것이든)
 *
 * 우선순위:
 *   1) source.axis_label_map[key]
 *   2) source.axes_config[key].label
 *   3) source.label  (단일 객체에 label이 있는 경우)
 *   4) key 그대로
 */
export function axisLabel(key, source) {
  if (!key) return '';
  if (source) {
    if (source.axis_label_map && source.axis_label_map[key]) {
      return source.axis_label_map[key];
    }
    if (source.axes_config && source.axes_config[key] && source.axes_config[key].label) {
      return source.axes_config[key].label;
    }
    if (source.axis === key && source.axis_label) return source.axis_label;
    if (source.axis === key && source.label) return source.label;
  }
  return key;
}

/**
 * 한 번에 여러 키 라벨 변환.
 */
export function axisLabels(keys, source) {
  return (keys || []).map((k) => axisLabel(k, source));
}

"""User-selected visual tolerance, applied per page before display rounding."""
VISUAL_IGNORE_BELOW_RATIO = 0.02
VISUAL_POLICY_NOTE = '按用户设置，每页视觉差异低于 2% 时忽略；达到或超过 2% 仍提示。缺页、尺寸、旋转和明确的文字/源清单差异不受该阈值影响。'


def visual_policy(ratio, has_difference):
    ignored = bool(has_difference and ratio is not None and 0 < ratio < VISUAL_IGNORE_BELOW_RATIO)
    return {'raw_visual_difference': bool(has_difference), 'visual_difference_ignored': ignored,
            'visual_ignore_below_ratio': VISUAL_IGNORE_BELOW_RATIO,
            'difference': bool(has_difference) and not ignored}

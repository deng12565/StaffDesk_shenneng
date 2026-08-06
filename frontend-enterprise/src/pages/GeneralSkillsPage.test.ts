// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  GENERAL_SKILL_MAX_ATTEMPTS,
  createGeneralSkillStreamWatchdog,
  generalSkillRunFailureMessage,
} from './GeneralSkillsPage';

afterEach(() => {
  vi.useRealTimers();
});

describe('General Skill stream guards', () => {
  it('resets the 30 second inactivity timeout for every event or heartbeat', () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const watchdog = createGeneralSkillStreamWatchdog(onTimeout);

    watchdog.touch();
    vi.advanceTimersByTime(29_000);
    expect(onTimeout).not.toHaveBeenCalled();

    watchdog.touch();
    vi.advanceTimersByTime(29_000);
    expect(onTimeout).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1_000);
    expect(onTimeout).toHaveBeenCalledTimes(1);
    watchdog.stop();
  });

  it('keeps the final concrete syntax error when the stream later disconnects', () => {
    expect(
      generalSkillRunFailureMessage(
        true,
        "第 2 次生成代码语法错误：SyntaxError: '(' was never closed",
        new DOMException('The operation was aborted', 'AbortError'),
      ),
    ).toBe(
      "第 2 次生成代码语法错误：SyntaxError: '(' was never closed；运行连接已中断（30 秒未收到服务端事件）。",
    );
    expect(GENERAL_SKILL_MAX_ATTEMPTS).toBe(3);
  });
});

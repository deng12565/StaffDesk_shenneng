import { useRef, useState } from 'react';
import { AlertCircle, Building2, LoaderCircle } from 'lucide-react';

import { api, TENANT_ID } from '../api/client';
import { setEnterpriseAuthSession, type EnterpriseAuthSession } from '../auth';
import avatarKnowledge from '../assets/staffdeck/staffdeck-avatar-knowledge.png';
import avatarCommerce from '../assets/staffdeck/staffdeck-avatar-commerce.png';
import avatarOps from '../assets/staffdeck/staffdeck-avatar-ops.png';
import avatarOverall from '../assets/staffdeck/staffdeck-avatar-overall.png';
import avatarQuality from '../assets/staffdeck/staffdeck-avatar-quality.png';
import avatarService from '../assets/staffdeck/staffdeck-avatar-service.png';
import shenergyLogo from '../assets/shenergy-logo-horizontal.png';
import IconFieldClear from '../assets/icons/field-clear.svg?react';
import IconFieldEye from '../assets/icons/field-eye.svg?react';
import IconFieldEyeOn from '../assets/icons/field-eye-on.svg?react';
import LanguageSwitcher from '../components/LanguageSwitcher';
import { Alert, AlertDescription, Button, Input, Label } from '../components/ui';
import { cn } from '../lib/utils';

export type LoginPageProps = {
  onLogin: (session: EnterpriseAuthSession) => void;
};

const EMPLOYEE_VISUALS = [
  { name: '综合协调', image: avatarOverall },
  { name: '运营管理', image: avatarOps },
  { name: '知识助理', image: avatarKnowledge },
  { name: '客户服务', image: avatarService },
  { name: '质量管理', image: avatarQuality },
  { name: '商务协作', image: avatarCommerce },
];

export default function LoginPage({ onLogin }: LoginPageProps) {
  const usernameInputRef = useRef<HTMLInputElement>(null);
  const passwordInputRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [usernameError, setUsernameError] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [formError, setFormError] = useState('');
  const [loading, setLoading] = useState(false);

  async function login() {
    if (loading) return;

    const trimmedUsername = username.trim();
    const trimmedPassword = password.trim();
    const nextUsernameError = trimmedUsername ? '' : '请输入账号';
    const nextPasswordError = trimmedPassword ? '' : '请输入密码';

    setUsernameError(nextUsernameError);
    setPasswordError(nextPasswordError);
    setFormError('');

    if (nextUsernameError || nextPasswordError) {
      if (nextUsernameError) usernameInputRef.current?.focus();
      else passwordInputRef.current?.focus();
      return;
    }

    setLoading(true);
    try {
      const session = await api.post<EnterpriseAuthSession>('/api/auth/login', {
        tenant_id: TENANT_ID,
        username: trimmedUsername,
        password: trimmedPassword,
      });
      setEnterpriseAuthSession(session);
      onLogin(session);
    } catch {
      setFormError('账号或密码不正确，请重试');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="grid min-h-[100svh] overflow-hidden bg-white lg:grid-cols-[minmax(0,1.18fr)_minmax(420px,0.82fr)]">
      <section className="relative flex min-h-[220px] overflow-hidden border-b border-[#dedbd5] bg-[#f2f1ed] px-[24px] py-[22px] sm:min-h-[250px] sm:px-[36px] lg:min-h-[100svh] lg:border-r lg:border-b-0 lg:px-[48px] lg:py-[38px] xl:px-[64px] xl:py-[46px]">
        <div className="relative z-10 mx-auto flex w-full max-w-[840px] flex-col">
          <img
            src={shenergyLogo}
            alt="申能 SHENERGY"
            className="h-auto w-[152px] select-none sm:w-[176px] lg:w-[196px]"
            draggable={false}
          />

          <div className="flex max-w-[520px] flex-col pt-[20px] sm:pt-[24px] lg:mt-[48px] lg:pt-0 xl:mt-[66px]">
            <span className="mb-[12px] h-[4px] w-[44px] bg-[#e60012]" aria-hidden="true" />
            <h1
              data-i18n-ignore
              className="text-[32px] leading-[1.16] font-semibold text-[#1b1818] sm:text-[38px] lg:text-[52px]"
            >
              小申数字员工
            </h1>
            <p className="mt-[10px] text-[15px] leading-[1.6] text-[#615d5c] sm:text-[16px] lg:mt-[14px] lg:text-[18px]">
              企业数字员工运营台
            </p>
          </div>

          <div className="mt-[28px] hidden min-h-0 flex-1 grid-cols-3 grid-rows-2 gap-[12px] lg:grid xl:mt-[34px] xl:max-w-[740px] xl:gap-[14px]">
            {EMPLOYEE_VISUALS.map((employee) => (
              <article
                key={employee.name}
                className="relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[8px] border border-[#dedbd5] bg-white/75 px-[14px] pt-[12px] xl:px-[16px] xl:pt-[14px]"
              >
                <div className="relative z-10 flex items-center justify-between gap-[8px]">
                  <strong className="truncate text-[13px] font-medium text-[#292525]">
                    {employee.name}
                  </strong>
                  <span className="flex shrink-0 items-center gap-[5px] text-[11px] text-[#706b69]">
                    <i className="size-[6px] rounded-full bg-[#e60012]" aria-hidden="true" />
                    运行中
                  </span>
                </div>
                <img
                  src={employee.image}
                  alt=""
                  className="pointer-events-none absolute right-1/2 bottom-[-10px] h-[150px] w-auto translate-x-1/2 object-contain xl:h-[clamp(180px,23vh,228px)]"
                  draggable={false}
                />
              </article>
            ))}
          </div>
        </div>

        <img
          src={avatarOverall}
          alt=""
          className="pointer-events-none absolute right-[14px] bottom-[-10px] h-[130px] w-auto object-contain opacity-90 sm:right-[36px] sm:h-[158px] lg:hidden"
          draggable={false}
        />
      </section>

      <section className="relative flex min-h-[calc(100svh-220px)] flex-col px-[24px] pb-[32px] sm:min-h-[calc(100svh-250px)] sm:px-[48px] lg:min-h-[100svh] lg:px-[40px] lg:pb-[44px] xl:px-[64px]">
        <div className="flex h-[72px] shrink-0 items-center justify-end lg:h-[92px]">
          <LanguageSwitcher />
        </div>

        <div className="mx-auto flex w-full max-w-[460px] flex-1 flex-col justify-center pb-[24px] motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2 motion-safe:duration-300 lg:pb-[48px] xl:pb-[72px]">
          <div>
            <div className="mb-[22px] hidden items-center gap-[10px] text-[12px] font-medium text-[#706b69] lg:flex">
              <Building2 className="size-[16px] text-[#e60012]" aria-hidden="true" />
              <span>申能集团内部工作入口</span>
              <span className="h-px flex-1 bg-[#dedbd5]" aria-hidden="true" />
            </div>
            <h2 id="login-heading" className="text-[30px] leading-[1.2] font-semibold text-[#1b1818] lg:text-[34px]">
              欢迎回来
            </h2>
            <p className="mt-[10px] text-[14px] leading-[1.7] text-[#706b69] lg:text-[15px]">
              登录后继续管理、协作与复盘。
            </p>
          </div>

          <form
            className="mt-[34px] flex flex-col lg:mt-[38px]"
            aria-labelledby="login-heading"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              void login();
            }}
          >
            <div>
              <Label htmlFor="login-username" className="text-[#292525]">
                账号
              </Label>
              <div className="relative mt-[10px]">
                <Input
                  ref={usernameInputRef}
                  id="login-username"
                  value={username}
                  autoComplete="username"
                  data-1p-ignore={undefined}
                  data-lpignore={undefined}
                  data-bwignore={undefined}
                  placeholder="请输入账号"
                  aria-invalid={Boolean(usernameError)}
                  aria-describedby={usernameError ? 'login-username-error' : undefined}
                  onChange={(event) => {
                    setUsername(event.target.value);
                    if (usernameError) setUsernameError('');
                    if (formError) setFormError('');
                  }}
                  className="h-[48px] rounded-[8px] border-[#cbc7c0] bg-white px-[14px] pr-[42px] text-[14px] text-[#1b1818] shadow-none placeholder:text-[#938e8b] focus-visible:border-[#706b69] focus-visible:ring-[#e60012]/15 lg:h-[52px]"
                />
                {username && (
                  <button
                    type="button"
                    aria-label="清空账号"
                    onClick={() => {
                      setUsername('');
                      setUsernameError('');
                      setFormError('');
                      usernameInputRef.current?.focus();
                    }}
                    className="absolute top-1/2 right-[13px] grid size-[20px] -translate-y-1/2 place-items-center text-[#837d79] outline-none hover:text-[#292525] focus-visible:ring-2 focus-visible:ring-[#e60012]/30"
                  >
                    <IconFieldClear className="size-[18px]" />
                  </button>
                )}
              </div>
              <p
                id="login-username-error"
                className={cn(
                  'mt-[7px] min-h-[18px] text-[12px] leading-[18px] text-[#c90010]',
                  !usernameError && 'invisible',
                )}
                aria-live="polite"
              >
                {usernameError || '请输入账号'}
              </p>
            </div>

            <div className="mt-[15px]">
              <Label htmlFor="login-password" className="text-[#292525]">
                密码
              </Label>
              <div className="relative mt-[10px]">
                <Input
                  ref={passwordInputRef}
                  id="login-password"
                  value={password}
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  data-1p-ignore={undefined}
                  data-lpignore={undefined}
                  data-bwignore={undefined}
                  placeholder="请输入密码"
                  aria-invalid={Boolean(passwordError)}
                  aria-describedby={passwordError ? 'login-password-error' : undefined}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    if (passwordError) setPasswordError('');
                    if (formError) setFormError('');
                  }}
                  className="h-[48px] rounded-[8px] border-[#cbc7c0] bg-white px-[14px] pr-[42px] text-[14px] text-[#1b1818] shadow-none placeholder:text-[#938e8b] focus-visible:border-[#706b69] focus-visible:ring-[#e60012]/15 lg:h-[52px]"
                />
                <button
                  type="button"
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  onClick={() => setShowPassword((previous) => !previous)}
                  className="absolute top-1/2 right-[13px] grid size-[20px] -translate-y-1/2 place-items-center text-[#837d79] outline-none hover:text-[#292525] focus-visible:ring-2 focus-visible:ring-[#e60012]/30"
                >
                  {showPassword ? (
                    <IconFieldEyeOn className="size-[18px]" />
                  ) : (
                    <IconFieldEye className="size-[18px]" />
                  )}
                </button>
              </div>
              <p
                id="login-password-error"
                className={cn(
                  'mt-[7px] min-h-[18px] text-[12px] leading-[18px] text-[#c90010]',
                  !passwordError && 'invisible',
                )}
                aria-live="polite"
              >
                {passwordError || '请输入密码'}
              </p>
            </div>

            {formError && (
              <Alert
                variant="destructive"
                aria-live="assertive"
                className="mt-[16px] border-[#f2c7ca] bg-[#fff7f7] px-[12px] py-[10px] text-[#b4000e]"
              >
                <AlertCircle />
                <AlertDescription className="text-[13px] text-[#b4000e]">
                  {formError}
                </AlertDescription>
              </Alert>
            )}

            <Button
              type="submit"
              size="lg"
              disabled={loading}
              className="mt-[24px] h-[48px] w-full rounded-[8px] bg-[#e60012] text-[15px] font-medium text-white hover:bg-[#c90010] focus-visible:ring-[#e60012]/30 lg:h-[52px]"
            >
              {loading && <LoaderCircle className="size-[17px] animate-spin" aria-hidden="true" />}
              {loading ? '登录中…' : '登录'}
            </Button>

            <p className="mt-[22px] border-t border-[#e7e3dd] pt-[16px] text-center text-[12px] leading-[1.7] text-[#837d79] lg:text-[13px]">
              首次部署可使用 admin / admin，登录后请立即修改密码。
            </p>
          </form>
        </div>
      </section>
    </main>
  );
}

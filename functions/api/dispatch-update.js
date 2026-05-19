const DEFAULT_REPOSITORY = "echohua6572/spy";
const DEFAULT_WORKFLOW = "update-history.yml";
const DEFAULT_REF = "main";

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export async function onRequestPost({ env }) {
  const token = env.GITHUB_TOKEN;
  const repository = env.GITHUB_REPOSITORY || DEFAULT_REPOSITORY;
  const workflow = env.GITHUB_WORKFLOW || DEFAULT_WORKFLOW;
  const ref = env.GITHUB_REF || DEFAULT_REF;

  if (!token) {
    return json({
      ok: false,
      error: "未配置 GITHUB_TOKEN。请在 Cloudflare Pages 环境变量中添加后再使用按钮。",
    }, 400);
  }

  const url = `https://api.github.com/repos/${repository}/actions/workflows/${workflow}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "accept": "application/vnd.github+json",
      "authorization": `Bearer ${token}`,
      "content-type": "application/json",
      "user-agent": "cloudflare-pages-spy-momentum-monitor",
      "x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify({ ref }),
  });

  if (response.status === 204) {
    return json({
      ok: true,
      message: "已触发 GitHub Actions 后台更新。通常需要几分钟完成。",
    });
  }

  const detail = await response.text();
  return json({
    ok: false,
    error: `GitHub 返回 ${response.status}: ${detail.slice(0, 240)}`,
  }, 502);
}

export async function onRequestGet() {
  return json({ ok: false, error: "Use POST" }, 405);
}

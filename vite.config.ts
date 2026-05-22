import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

function rewriteStaticPaths() {
	return {
		name: 'rewrite-static-paths',
		configureServer(server) {
			server.middlewares.use((req, _res, next) => {
				if (req.url?.startsWith('/static/') && !req.url.startsWith('/static/assets/')) {
					req.url = req.url.replace('/static/', '/');
				}
				next();
			});
		}
	};
}

export default defineConfig({
	plugins: [
		rewriteStaticPaths(),
		sveltekit()
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});

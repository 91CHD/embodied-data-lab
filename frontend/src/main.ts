import { createApp } from 'vue';
import { createRouter, createWebHistory } from 'vue-router';
import 'element-plus/dist/index.css';
import './style.css';
import App from './App.vue';
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/datasets' },
    { path: '/datasets', component: () => import('./pages/DatasetsPage.vue') },
    { path: '/datasets/:id', component: () => import('./pages/DatasetPage.vue') },
    { path: '/datasets/:id/episodes/:index', component: () => import('./pages/EpisodePage.vue') },
    { path: '/jobs', component: () => import('./pages/JobsPage.vue') },
    { path: '/jobs/:id', component: () => import('./pages/JobPage.vue') },
    { path: '/experiments', component: () => import('./pages/ExperimentsPage.vue') },
    { path: '/experiments/:id', component: () => import('./pages/ExperimentPage.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/datasets' },
  ],
});
createApp(App).use(router).mount('#app');

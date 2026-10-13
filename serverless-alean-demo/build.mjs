import {mkdir,copyFile,writeFile,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {CONFIG,assertDemo} from './tgcloud/lib/aleanConfig.js';
assertDemo();
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.dirname(here);
const revision=process.env.ALEAN_RELEASE_SHA||execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
if(!/^[0-9a-f]{40}$/.test(revision))throw new Error('Exact commit required');
const site=path.join(here,'site');await mkdir(site,{recursive:true});
const assets={'index.html':'alean.html','alean.html':'alean.html','alean.js':'alean.js','alean.css':'alean.css','styles.css':'styles.css'};
const manifest={};
for(const [destination,source] of Object.entries(assets)) {
  const input=path.join(root,'miniapp',source);await copyFile(input,path.join(site,destination));
  manifest[destination]=createHash('sha256').update(await readFile(input)).digest('hex');
}
await writeFile(path.join(site,'release.json'),JSON.stringify({revision,bot:'apreltour_bot',runtime:'telegram-serverless',config:CONFIG,assets:manifest},null,2)+'\n');
// Release metadata is stamped in an isolated archive, never a developer checkout.
if(process.env.ALEAN_STAMP_RELEASE==='true') {
  await writeFile(path.join(here,'tgcloud/lib/aleanRelease.js'),
    `export const REVISION = '${revision}';\nexport const MINI_APP_URL = 'https://app8886586430.tgcloud.ai/';\n`);
}
console.log(JSON.stringify({revision,assets:Object.keys(manifest),config:CONFIG}));

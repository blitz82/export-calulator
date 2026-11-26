// Historical HTML-output QA only; not the current settlement PDF workflow.
import fs from 'node:fs/promises'
import {chromium} from 'playwright'
const browser=await chromium.launch({channel:'msedge',headless:true})
try{
  const page=await browser.newPage()
  await page.setContent(await fs.readFile('output/sorting-tests/form-preview.html','utf8'))
  await page.emulateMedia({media:'print'})
  await page.pdf({path:'output/sorting-tests/form-preview.pdf',printBackground:true,preferCSSPageSize:true})
}finally{await browser.close()}
